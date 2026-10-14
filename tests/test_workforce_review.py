"""Regression checks for findings from the production functions and logic review."""

import csv
from datetime import date, datetime, timedelta
from decimal import Decimal
from io import StringIO

import pytest
from conftest import TEST_PASSWORD
from flask import template_rendered

from app.extensions import db
from app.models import (
    AuditLog,
    Employee,
    Notification,
    OperationalReview,
    PayrollRecord,
    Role,
    ScheduledShift,
    User,
)
from app.payroll_review import POLICY_SCOPE, minimum_wage
from app.routes.reports import get_period_bounds

MONDAY = date(2026, 10, 5)
SCHEDULE_WEEK = date(2026, 10, 19)


def login(client, username="admin_user"):
    assert (
        client.post("/login", data={"username": username, "password": TEST_PASSWORD}).status_code
        == 302
    )


@pytest.fixture()
def reviewed_record(app, users):
    app.config.update(REQUIRE_PAYROLL_REVIEW=True, WORKFORCE_TIMEZONE="America/New_York")
    with app.app_context():
        record = PayrollRecord.build(
            db.session.get(Employee, users["employee_profile_id"]), MONDAY, 8
        )
        db.session.add_all(
            [
                record,
                OperationalReview(
                    review_type="virginia_payroll_policy",
                    scope=POLICY_SCOPE,
                    reference="Synthetic policy fixture; no real payroll sign-off",
                    reviewed_by_id=users["admin_id"],
                ),
            ]
        )
        db.session.commit()
        return record.id, record.version_id


@pytest.mark.parametrize(
    "work_date,expected",
    [
        (date(2026, 1, 1), "12.77"),
        (date(2026, 12, 31), "12.77"),
        (date(2027, 1, 1), "13.75"),
        (date(2027, 12, 31), "13.75"),
        (date(2028, 1, 1), "15.00"),
        (date(2028, 12, 31), "15.00"),
    ],
)
def test_verified_virginia_rate_effective_dates(work_date, expected):
    assert minimum_wage(work_date) == Decimal(expected)


@pytest.mark.parametrize("work_date", [date(2024, 1, 1), date(2025, 12, 31), date(2029, 1, 1)])
def test_policy_never_guesses_unreviewed_historical_or_future_rates(work_date):
    with pytest.raises(ValueError, match="2026-2028"):
        minimum_wage(work_date)


def test_payroll_approval_requires_actual_matching_policy_review(
    client, app, users, reviewed_record
):
    record_id, version = reviewed_record
    with app.app_context():
        OperationalReview.query.one().scope = "Different store policy"
        db.session.commit()
    login(client)
    response = client.post(
        f"/payroll/{record_id}/review", data={"action": "approve", "version_id": version}
    )
    assert response.status_code == 409
    assert b"Payroll readiness" in response.data
    with app.app_context():
        assert db.session.get(PayrollRecord, record_id).status == "Pending"
        assert AuditLog.query.filter_by(action_type="payroll_reviewed").count() == 0


def test_old_policy_without_single_location_scope_cannot_authorize_approval(
    client, app, users, reviewed_record
):
    record_id, version = reviewed_record
    with app.app_context():
        OperationalReview.query.one().scope = POLICY_SCOPE.replace(
            "; single-location work for this employer", ""
        )
        db.session.commit()
    login(client)
    assert (
        client.post(
            f"/payroll/{record_id}/review", data={"action": "approve", "version_id": version}
        ).status_code
        == 409
    )


@pytest.mark.parametrize(
    "changes",
    [
        dict(rate_snapshot=Decimal("12.76")),
        dict(multiplier_snapshot=Decimal("1.49")),
        dict(work_date=date(2025, 12, 31)),
        dict(work_date=date(2029, 1, 1)),
    ],
)
def test_outside_policy_approval_is_atomic(client, app, users, reviewed_record, changes):
    record_id, _ = reviewed_record
    with app.app_context():
        record = db.session.get(PayrollRecord, record_id)
        for key, value in changes.items():
            setattr(record, key, value)
        db.session.commit()
        version = record.version_id
    login(client)
    assert (
        client.post(
            f"/payroll/{record_id}/review", data={"action": "approve", "version_id": version}
        ).status_code
        == 409
    )
    with app.app_context():
        assert db.session.get(PayrollRecord, record_id).status == "Pending"
        assert Notification.query.filter_by(user_id=users["employee_id"]).count() == 0


@pytest.mark.parametrize("other_kind", ["import", "different_rate", "different_multiplier"])
def test_whole_workweek_guard_checks_imports_and_multiple_rates(
    client, app, users, reviewed_record, other_kind
):
    record_id, version = reviewed_record
    with app.app_context():
        employee = db.session.get(Employee, users["employee_profile_id"])
        other = PayrollRecord.build(employee, MONDAY + timedelta(days=1), 8)
        if other_kind == "import":
            other.legacy_import = True
        elif other_kind == "different_rate":
            other.rate_snapshot = Decimal("35")
            other.total_pay = Decimal("280")
        else:
            other.multiplier_snapshot = Decimal("2")
        db.session.add(other)
        db.session.commit()
    login(client)
    response = client.post(
        f"/payroll/{record_id}/review", data={"action": "approve", "version_id": version}
    )
    assert response.status_code == 409
    assert b"imported records" in response.data or b"multiple compensation rates" in response.data
    with app.app_context():
        assert db.session.get(PayrollRecord, record_id).status == "Pending"


def test_matching_policy_allows_reviewed_hourly_entry(client, app, users, reviewed_record):
    record_id, version = reviewed_record
    login(client)
    assert (
        client.post(
            f"/payroll/{record_id}/review", data={"action": "approve", "version_id": version}
        ).status_code
        == 302
    )
    with app.app_context():
        record = db.session.get(PayrollRecord, record_id)
        assert record.status == "Approved" and record.reviewed_by_id == users["admin_id"]
        assert (
            Notification.query.filter_by(user_id=users["employee_id"]).one().title
            == "Timesheet updated"
        )


@pytest.mark.parametrize("operation", ["review", "delete"])
@pytest.mark.parametrize("submitted", ["missing", "stale"])
def test_outdated_payroll_approval_or_void_cannot_touch_edited_hours(
    client, app, users, payroll_record, operation, submitted
):
    with app.app_context():
        record = db.session.get(PayrollRecord, payroll_record)
        old_version = record.version_id
        record.hours_worked = Decimal("7")
        PayrollRecord.recalculate_week(record.employee_id, record.work_date)
        db.session.commit()
        new_version = record.version_id
    login(client)
    data = dict(action="approve")
    if submitted == "stale":
        data["version_id"] = old_version
    assert client.post(f"/payroll/{payroll_record}/{operation}", data=data).status_code == 409
    with app.app_context():
        record = db.session.get(PayrollRecord, payroll_record)
        assert record.version_id == new_version and record.status == "Pending"
        assert record.hours_worked == 7
        assert (
            AuditLog.query.filter(
                AuditLog.action_type.in_(["payroll_reviewed", "payroll_deleted"])
            ).count()
            == 0
        )


def test_privileged_staff_cannot_void_their_own_timesheet(client, app, users, payroll_record):
    with app.app_context():
        db.session.get(User, users["employee_id"]).role = Role.PAYROLL
        db.session.commit()
        version = db.session.get(PayrollRecord, payroll_record).version_id
    login(client, "employee_user")
    assert (
        client.post(f"/payroll/{payroll_record}/delete", data={"version_id": version}).status_code
        == 403
    )
    with app.app_context():
        assert db.session.get(PayrollRecord, payroll_record).status == "Pending"
        assert AuditLog.query.filter_by(action_type="payroll_deleted").count() == 0


def test_approved_entry_cannot_be_downgraded_by_old_review_controls(
    client, app, users, payroll_record
):
    login(client)
    with app.app_context():
        record = db.session.get(PayrollRecord, payroll_record)
        record.status = "Approved"
        db.session.commit()
        version = record.version_id
    assert (
        client.post(
            f"/payroll/{payroll_record}/review", data={"action": "review", "version_id": version}
        ).status_code
        == 409
    )
    with app.app_context():
        assert db.session.get(PayrollRecord, payroll_record).status == "Approved"


def test_employee_identity_is_never_clamped_to_pagination_limit(client, app, users):
    with app.app_context():
        for identity in (500, 501):
            db.session.add(
                Employee(
                    id=identity,
                    first_name="Staff",
                    last_name=str(identity),
                    email=f"staff{identity}@example.invalid",
                )
            )
        db.session.commit()
    login(client)
    assert (
        client.post(
            "/payroll", data=dict(employee_id="501", work_date=MONDAY.isoformat(), hours_worked="8")
        ).status_code
        == 302
    )
    with app.app_context():
        record = PayrollRecord.query.one()
        assert record.employee_id == 501
        assert AuditLog.query.filter_by(action_type="payroll_created").one().target_id == str(
            record.id
        )
    assert b"No payroll entries yet" not in client.get("/payroll?employee_id=501").data
    assert b"No payroll entries yet" in client.get("/payroll?employee_id=500").data
    assert client.get("/payroll?employee_id=" + "9" * 50).status_code == 400


def test_store_date_drives_hours_today_and_report_week_at_utc_midnight(
    client, app, users, monkeypatch
):
    # UTC Monday is still Sunday evening in Virginia; the report must remain in the previous week.
    app.config["WORKFORCE_TIMEZONE"] = "America/New_York"
    monkeypatch.setattr("app.models.utcnow", lambda: datetime(2026, 10, 12, 1, 0))
    login(client)
    page = client.get("/payroll")
    assert b'max="2026-10-11"' in page.data
    assert b'workDate.value = "2026-10-11"' in page.data
    client.post(
        "/payroll",
        data=dict(
            employee_id=users["employee_profile_id"], work_date="2026-10-12", hours_worked="8"
        ),
    )
    with app.app_context():
        assert PayrollRecord.query.count() == 0
        assert get_period_bounds("weekly") == (MONDAY, date(2026, 10, 11))
    client.post(
        "/payroll",
        data=dict(
            employee_id=users["employee_profile_id"], work_date="2026-10-11", hours_worked="8"
        ),
    )
    with app.app_context():
        assert PayrollRecord.query.one().work_date == date(2026, 10, 11)


def test_csv_exposes_approval_and_import_status_instead_of_implying_pay_ready(client, app, users):
    with app.app_context():
        employee = db.session.get(Employee, users["employee_profile_id"])
        rows = [PayrollRecord.build(employee, MONDAY + timedelta(days=i), 8) for i in range(3)]
        rows[1].status = "Approved"
        rows[2].legacy_import = True
        db.session.add_all(rows)
        db.session.commit()
    login(client)
    response = client.get("/reports/export.csv?start_date=2026-10-05&end_date=2026-10-07")
    rows = list(csv.DictReader(StringIO(response.data.decode())))
    assert [row["Status"] for row in rows] == ["Pending", "Approved", "Pending"]
    assert [row["Imported - reconciliation required"] for row in rows] == ["No", "No", "Yes"]


def test_operational_payroll_totals_and_trends_exclude_voided_and_rejected_entries(
    client, app, users, monkeypatch
):
    monkeypatch.setattr("app.models.utcnow", lambda: datetime(2026, 10, 9, 14))
    with app.app_context():
        employee = db.session.get(Employee, users["employee_profile_id"])
        rows = [PayrollRecord.build(employee, MONDAY + timedelta(days=i), 8) for i in range(3)]
        rows[1].status = "Rejected"
        rows[2].status = "Voided"
        rows[2].overtime_hours = Decimal("2")
        rows[2].regular_hours = Decimal("6")
        db.session.add_all(rows)
        db.session.commit()
    login(client)
    contexts = []

    def capture(sender, template, context, **extra):
        contexts.append(context)

    with template_rendered.connected_to(capture, app):
        assert client.get("/management/overview").status_code == 200
    assert contexts[0]["stats"]["payroll_total_week"] == Decimal("240")
    assert contexts[0]["stats"]["overtime_rate"] == 0
    assert contexts[0]["payroll_totals"] == [240.0]


@pytest.mark.parametrize("username", ["admin_user", "employee_user"])
def test_payroll_summary_excludes_rejected_and_voided_entries_but_keeps_rejection_visible(
    client, app, users, username
):
    with app.app_context():
        employee = db.session.get(Employee, users["employee_profile_id"])
        rows = [
            PayrollRecord.build(employee, MONDAY, 8),
            PayrollRecord.build(employee, MONDAY + timedelta(days=1), 12),
            PayrollRecord.build(employee, MONDAY + timedelta(days=2), 14),
        ]
        rows[1].status = "Rejected"
        rows[2].status = "Voided"
        db.session.add_all(rows)
        db.session.commit()
    login(client, username)
    contexts = []

    def capture(sender, template, context, **extra):
        contexts.append(context)

    with template_rendered.connected_to(capture, app):
        response = client.get("/payroll")
    assert response.status_code == 200
    assert b"2026-10-06" in response.data and b"Rejected" in response.data
    assert b"2026-10-07" not in response.data
    assert [record.status for record in contexts[0]["records"]] == ["Rejected", "Pending"]
    assert contexts[0]["summary"] == dict(
        hours=8.0, regular_hours=8.0, overtime_hours=0.0, total_pay=240.0
    )


def schedule_data(users, **changes):
    data = dict(
        employee_id=users["employee_profile_id"],
        week=SCHEDULE_WEEK.isoformat(),
        simple_schedule="1",
        shift_days=["0", "2", "4"],
        start_time="09:00",
        end_time="17:00",
        notes="",
    )
    return dict(data, **changes)


def test_schedule_batch_posts_one_private_notification_and_reassigns_both_people(
    client, app, users
):
    app.config["WORKFORCE_TIMEZONE"] = "America/New_York"
    login(client)
    assert client.post("/schedule", data=schedule_data(users)).status_code == 302
    with app.app_context():
        note = Notification.query.filter_by(user_id=users["employee_id"], category="schedule").one()
        assert "Oct 19" in note.message and "Oct 21" in note.message and "Oct 23" in note.message
        assert "09:00 AM" in note.message and "America/New_York" in note.message
        assert note.url == "/schedule?week=2026-10-19"
        shift = ScheduledShift.query.order_by(ScheduledShift.starts_at).first()
        identity, version = shift.id, shift.version_id
    assert (
        client.post(
            "/schedule",
            data=schedule_data(
                users,
                employee_id=users["admin_employee_id"],
                shift_id=identity,
                version_id=version,
                shift_date="2026-10-19",
                shift_days=[],
            ),
        ).status_code
        == 302
    )
    with app.app_context():
        former = Notification.query.filter_by(
            user_id=users["employee_id"], title="Shift reassigned"
        ).one()
        added = Notification.query.filter_by(user_id=users["admin_id"], title="Shift changed").one()
        assert "Oct 19" in former.message and "Oct 19" in added.message
        assert former.url == added.url == "/schedule?week=2026-10-19"


def test_schedule_noop_or_failed_batch_never_posts_misleading_notifications(client, app, users):
    login(client)
    assert client.post("/schedule", data=schedule_data(users, shift_days=["0"])).status_code == 302
    with app.app_context():
        shift = ScheduledShift.query.one()
        identity, version = shift.id, shift.version_id
        assert Notification.query.filter_by(category="schedule").count() == 1
    assert (
        client.post(
            "/schedule",
            data=schedule_data(
                users, shift_id=identity, version_id=version, shift_date="2026-10-19", shift_days=[]
            ),
        ).status_code
        == 302
    )
    assert client.post("/schedule", data=schedule_data(users)).status_code == 400
    with app.app_context():
        assert ScheduledShift.query.count() == 1
        assert Notification.query.filter_by(category="schedule").count() == 1
        version = db.session.get(ScheduledShift, identity).version_id
    assert (
        client.post(
            f"/schedule/{identity}/cancel", data=dict(version_id=version, week="2026-10-26")
        ).status_code
        == 302
    )
    with app.app_context():
        note = Notification.query.filter_by(title="Shift cancelled").one()
        assert note.user_id == users["employee_id"] and "Oct 19" in note.message
        assert note.url == "/schedule?week=2026-10-19"
