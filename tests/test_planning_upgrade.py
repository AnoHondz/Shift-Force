"""Meaningful planning/privacy, coverage and punch reconciliation regressions."""

import json
import re
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from conftest import TEST_PASSWORD
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    Attendance,
    AvailabilityProfile,
    Employee,
    LeaveRequest,
    Notification,
    PayrollRecord,
    Role,
    ScheduledShift,
    ShiftCoverage,
    User,
)
from app.planning import labor_projection
from app.scheduling import monday
from app.time_review import time_preview
from app.utils import store_today


def login(client, username="admin_user"):
    return client.post("/login", data=dict(username=username, password=TEST_PASSWORD))


def hidden(response, name):
    return re.search(r'name="' + name + r'"[^>]*value="([^"]+)"', response.get_data(as_text=True))[
        1
    ]


@pytest.fixture()
def setup(app, users):
    app.config["WORKFORCE_TIMEZONE"] = "America/New_York"
    with app.app_context():
        week = monday(store_today() + timedelta(days=7))
        coworker = Employee(
            first_name="Coverage",
            last_name="Coworker",
            email="cover@example.com",
            job_title="Staff",
            hourly_rate=20,
            overtime_multiplier=1.5,
        )
        db.session.add(coworker)
        db.session.flush()
        account = User(username="coverage_user", role=Role.EMPLOYEE, employee_id=coworker.id)
        account.set_password(TEST_PASSWORD)
        db.session.add(account)
        db.session.commit()
        return {**users, "week": week, "coworker_id": coworker.id, "coworker_user_id": account.id}


def instant(day, hour, minute=0):
    return (
        datetime.combine(day, time(hour, minute), ZoneInfo("America/New_York"))
        .astimezone(timezone.utc)
        .replace(tzinfo=None)
    )


def shift(app, employee, day, start=9, end=17, published=True, notes="private-plan"):
    with app.app_context():
        row = ScheduledShift(
            employee_id=employee,
            starts_at=instant(day, start),
            ends_at=instant(day, end),
            published=published,
            notes=notes,
        )
        db.session.add(row)
        db.session.commit()
        return row.id


def availability_values(**changes):
    values = {f"mode_{day}": "any" for day in range(7)}
    return dict(version_id="0", **values, **changes)


def make_punch(app, employee, day, start=9, end=17):
    with app.app_context():
        row = Attendance(employee_id=employee, date=day, clock_in_at=instant(day, start))
        row.complete_shift(instant(day, end))
        db.session.add(row)
        db.session.commit()
        return row.id


def test_draft_is_private_until_publish_and_notifies_once(client, app, setup):
    login(client)
    response = client.post(
        "/schedule",
        data=dict(
            simple_schedule="1",
            employee_id=setup["employee_profile_id"],
            week=setup["week"].isoformat(),
            shift_days="0",
            start_time="09:00",
            end_time="17:00",
            notes="unpublished-secret",
            publish="0",
        ),
    )
    assert response.status_code == 302
    with app.app_context():
        assert ScheduledShift.query.one().published is False
        assert Notification.query.filter_by(category="schedule").count() == 0
    staff = app.test_client()
    login(staff, "employee_user")
    path = "/schedule?week=" + setup["week"].isoformat()
    assert b"unpublished-secret" not in staff.get(path).data
    assert b"unpublished-secret" not in staff.get("/employee/dashboard").data
    assert (
        b"unpublished-secret"
        not in staff.get("/schedule/calendar.ics?week=" + setup["week"].isoformat()).data
    )
    page = client.get(path)
    response = client.post(
        "/schedule/publish",
        data=dict(week=setup["week"].isoformat(), draft_token=hidden(page, "draft_token")),
    )
    assert response.status_code == 302
    assert b"unpublished-secret" in staff.get(path).data
    with app.app_context():
        assert (
            Notification.query.filter_by(
                user_id=setup["employee_id"], title="Schedule published"
            ).count()
            == 1
        )
    assert (
        client.post(
            "/schedule/publish",
            data=dict(week=setup["week"].isoformat(), draft_token=hidden(page, "draft_token")),
        ).status_code
        == 409
    )


@pytest.mark.parametrize("changed", ["shift", "leave", "availability", "archived"])
def test_publish_rechecks_whole_batch_and_never_partially_publishes(client, app, setup, changed):
    first = shift(app, setup["employee_profile_id"], setup["week"], published=False)
    shift(app, setup["coworker_id"], setup["week"], published=False)
    login(client)
    page = client.get("/schedule?week=" + setup["week"].isoformat())
    with app.app_context():
        if changed == "shift":
            db.session.get(ScheduledShift, first).notes = "new manager edit"
        elif changed == "leave":
            db.session.add(
                LeaveRequest(
                    employee_id=setup["employee_profile_id"],
                    request_type="Vacation",
                    start_date=setup["week"],
                    end_date=setup["week"],
                    status="Approved",
                )
            )
        elif changed == "availability":
            rules = [dict(mode="off", start=0, end=1440) for _ in range(7)]
            db.session.add(
                AvailabilityProfile(
                    employee_id=setup["employee_profile_id"], rules=json.dumps(rules)
                )
            )
        else:
            db.session.get(Employee, setup["employee_profile_id"]).archived = True
        db.session.commit()
    assert (
        client.post(
            "/schedule/publish",
            data=dict(week=setup["week"].isoformat(), draft_token=hidden(page, "draft_token")),
        ).status_code
        == 409
    )
    with app.app_context():
        assert all(not row.published for row in ScheduledShift.query.all())
        assert Notification.query.filter_by(title="Schedule published").count() == 0


def test_copy_is_private_preserves_wall_clock_and_rejects_repeat(client, app, setup):
    shift(app, setup["employee_profile_id"], setup["week"] - timedelta(days=7))
    login(client)
    page = client.get("/schedule?week=" + setup["week"].isoformat())
    form = dict(week=setup["week"].isoformat(), copy_token=hidden(page, "copy_token"))
    assert client.post("/schedule/copy", data=form).status_code == 302
    assert client.post("/schedule/copy", data=form).status_code == 409
    with app.app_context():
        rows = ScheduledShift.query.order_by(ScheduledShift.starts_at).all()
        assert len(rows) == 2 and rows[0].published and not rows[1].published
        assert rows[1].starts_at == instant(setup["week"], 9)
        assert Notification.query.filter_by(category="schedule").count() == 0


def test_copy_blocked_by_one_person_leaves_other_shifts_unwritten(client, app, setup):
    for person in [setup["employee_profile_id"], setup["coworker_id"]]:
        shift(app, person, setup["week"] - timedelta(days=7))
    with app.app_context():
        db.session.add(
            LeaveRequest(
                employee_id=setup["coworker_id"],
                request_type="Vacation",
                start_date=setup["week"],
                end_date=setup["week"],
                status="Approved",
            )
        )
        db.session.commit()
    login(client)
    page = client.get("/schedule?week=" + setup["week"].isoformat())
    assert (
        client.post(
            "/schedule/copy",
            data=dict(week=setup["week"].isoformat(), copy_token=hidden(page, "copy_token")),
        ).status_code
        == 409
    )
    with app.app_context():
        assert ScheduledShift.query.count() == 2


def test_copy_across_clock_change_keeps_local_hours(client, app, setup):
    day = datetime(2026, 10, 26).date()
    shift(app, setup["employee_profile_id"], day)
    login(client)
    target = day + timedelta(days=7)
    page = client.get("/schedule?week=" + target.isoformat())
    assert (
        client.post(
            "/schedule/copy",
            data=dict(week=target.isoformat(), copy_token=hidden(page, "copy_token")),
        ).status_code
        == 302
    )
    with app.app_context():
        rows = ScheduledShift.query.order_by(ScheduledShift.starts_at).all()
        assert rows[1].starts_at == instant(target, 9)
        assert rows[1].starts_at - rows[0].starts_at == timedelta(days=7, hours=1)


def test_availability_is_self_owned_versioned_and_blocks_scheduling(client, app, setup):
    login(client, "employee_user")
    values = availability_values()
    values["mode_0"] = "off"
    values["employee_id"] = str(setup["admin_employee_id"])
    assert client.post("/schedule/availability", data=values).status_code == 302
    assert client.post("/schedule/availability", data=values).status_code == 409
    with app.app_context():
        assert AvailabilityProfile.query.one().employee_id == setup["employee_profile_id"]
    admin = app.test_client()
    login(admin)
    response = admin.post(
        "/schedule",
        data=dict(
            employee_id=setup["employee_profile_id"],
            starts_at=setup["week"].isoformat() + "T09:00",
            ends_at=setup["week"].isoformat() + "T17:00",
        ),
    )
    assert response.status_code == 400 and b"Outside submitted availability" in response.data


@pytest.mark.parametrize(
    "changes",
    [
        {"mode_0": "invalid"},
        {"mode_0": "hours", "start_0": "25:00", "end_0": "17:00"},
        {"mode_0": "hours", "start_0": "18:00", "end_0": "09:00"},
        {"mode_0": "hours", "start_0": "09:00", "end_0": "09:00"},
    ],
)
def test_invalid_availability_has_no_partial_write(client, app, setup, changes):
    login(client, "employee_user")
    values = availability_values()
    values.update(changes)
    assert client.post("/schedule/availability", data=values).status_code == 400
    with app.app_context():
        assert AvailabilityProfile.query.count() == 0


def offered(app, setup):
    identity = shift(app, setup["employee_profile_id"], setup["week"])
    staff = app.test_client()
    login(staff, "employee_user")
    assert staff.post(f"/schedule/{identity}/offer", data=dict(version_id="1")).status_code == 302
    with app.app_context():
        return identity, ShiftCoverage.query.one().id


def test_coverage_needs_independent_approval_and_rechecks_claimant(client, app, setup):
    identity, offer_id = offered(app, setup)
    claimant = app.test_client()
    login(claimant, "coverage_user")
    assert (
        claimant.post(f"/schedule/coverage/{offer_id}/claim", data=dict(version_id="1")).status_code
        == 302
    )
    assert (
        claimant.post(
            f"/schedule/coverage/{offer_id}/review", data=dict(version_id="2", action="approve")
        ).status_code
        == 403
    )
    with app.app_context():
        assert db.session.get(ScheduledShift, identity).employee_id == setup["employee_profile_id"]
    login(client)
    assert (
        client.post(
            f"/schedule/coverage/{offer_id}/review", data=dict(version_id="2", action="approve")
        ).status_code
        == 302
    )
    with app.app_context():
        assert db.session.get(ScheduledShift, identity).employee_id == setup["coworker_id"]
        assert db.session.get(ShiftCoverage, offer_id).status == "Approved"
        assert Notification.query.filter_by(title="Coverage approved").count() == 1
        assert Notification.query.filter_by(title="Your coverage is approved").count() == 1
    assert (
        client.post(
            f"/schedule/coverage/{offer_id}/review", data=dict(version_id="2", action="approve")
        ).status_code
        == 409
    )


@pytest.mark.parametrize("change", ["overlap", "leave", "availability"])
def test_coverage_approval_rechecks_new_conflicts(client, app, setup, change):
    identity, offer_id = offered(app, setup)
    claimant = app.test_client()
    login(claimant, "coverage_user")
    claimant.post(f"/schedule/coverage/{offer_id}/claim", data=dict(version_id="1"))
    with app.app_context():
        if change == "overlap":
            db.session.add(
                ScheduledShift(
                    employee_id=setup["coworker_id"],
                    starts_at=instant(setup["week"], 10),
                    ends_at=instant(setup["week"], 11),
                )
            )
        elif change == "leave":
            db.session.add(
                LeaveRequest(
                    employee_id=setup["coworker_id"],
                    start_date=setup["week"],
                    end_date=setup["week"],
                    request_type="Vacation",
                    status="Approved",
                )
            )
        else:
            db.session.add(
                AvailabilityProfile(
                    employee_id=setup["coworker_id"],
                    rules=json.dumps([dict(mode="off", start=0, end=1440) for _ in range(7)]),
                )
            )
        db.session.commit()
    login(client)
    assert (
        client.post(
            f"/schedule/coverage/{offer_id}/review", data=dict(version_id="2", action="approve")
        ).status_code
        == 409
    )
    with app.app_context():
        assert db.session.get(ScheduledShift, identity).employee_id == setup["employee_profile_id"]
        assert db.session.get(ShiftCoverage, offer_id).status == "Pending"


def test_editing_offered_shift_closes_request(client, app, setup):
    identity, offer_id = offered(app, setup)
    login(client)
    response = client.post(
        "/schedule",
        data=dict(
            shift_id=identity,
            version_id="1",
            employee_id=setup["employee_profile_id"],
            starts_at=setup["week"].isoformat() + "T10:00",
            ends_at=setup["week"].isoformat() + "T18:00",
        ),
    )
    assert response.status_code == 302
    with app.app_context():
        assert db.session.get(ShiftCoverage, offer_id).status == "Cancelled"


def test_active_offer_uniqueness_is_database_enforced(app, setup):
    identity, offer_id = offered(app, setup)
    with app.app_context():
        db.session.add(
            ShiftCoverage(
                shift_id=identity, offered_by_id=setup["employee_profile_id"], shift_version=1
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
        assert ShiftCoverage.query.count() == 1


def test_projection_is_weekly_decimal_and_clips_overnight(app, setup):
    for offset in range(6):
        shift(app, setup["employee_profile_id"], setup["week"] + timedelta(days=offset))
    with app.app_context():
        projection = labor_projection(
            ScheduledShift.query.all(), setup["week"], ZoneInfo("America/New_York")
        )
        assert projection["cost"] == Decimal("1560.00")
        assert projection["overtime"][0]["hours"] == 8


def test_personal_calendar_escapes_text_and_excludes_other_staff_and_drafts(client, app, setup):
    shift(
        app,
        setup["employee_profile_id"],
        setup["week"],
        notes="front,desk;\\\nBEGIN:VEVENT\n" + "🌙" * 90,
    )
    shift(app, setup["employee_profile_id"], setup["week"] + timedelta(days=1), published=False)
    shift(app, setup["coworker_id"], setup["week"])
    login(client, "employee_user")
    response = client.get(
        "/schedule/calendar.ics?week="
        + setup["week"].isoformat()
        + "&staff="
        + str(setup["coworker_id"])
    )
    assert response.status_code == 200 and response.mimetype == "text/calendar"
    assert response.data.count(b"BEGIN:VEVENT\r\n") == 1
    assert b"front\\,desk\\;" in response.data
    assert all(len(line) <= 75 for line in response.data.split(b"\r\n"))
    assert response.headers["Cache-Control"] == "no-store"


def payroll_preview(client, setup, week):
    return client.get(
        "/payroll/clock-hours",
        query_string=dict(employee_id=setup["employee_profile_id"], week=week.isoformat()),
    )


def prepare(client, setup, week, page):
    return client.post(
        "/payroll/clock-hours",
        data=dict(
            employee_id=setup["employee_profile_id"],
            week=week.isoformat(),
            source_token=hidden(page, "source_token"),
            confirmed="1",
        ),
    )


def test_clock_preparation_is_pending_reviewed_source_and_no_duplicate(client, app, setup):
    day = setup["week"] - timedelta(days=7)
    make_punch(app, setup["employee_profile_id"], day)
    login(client)
    page = payroll_preview(client, setup, day)
    assert prepare(client, setup, day, page).status_code == 302
    assert prepare(client, setup, day, page).status_code == 409
    fresh = payroll_preview(client, setup, day)
    assert prepare(client, setup, day, fresh).status_code == 302
    with app.app_context():
        row = PayrollRecord.query.one()
        assert row.hours_worked == 8 and row.status == "Pending" and row.attendance_fingerprint
        assert Notification.query.filter_by(title="Clock hours prepared").count() == 1


def test_changed_clock_source_invalidates_preview_and_approval(client, app, setup):
    day = setup["week"] - timedelta(days=7)
    identity = make_punch(app, setup["employee_profile_id"], day)
    login(client)
    page = payroll_preview(client, setup, day)
    with app.app_context():
        db.session.get(Attendance, identity).clock_out_at = instant(day, 18)
        db.session.commit()
    assert prepare(client, setup, day, page).status_code == 409
    assert prepare(client, setup, day, payroll_preview(client, setup, day)).status_code == 302
    with app.app_context():
        row = PayrollRecord.query.one()
        payroll_id, version = row.id, row.version_id
        db.session.get(Attendance, identity).clock_out_at = instant(day, 19)
        db.session.commit()
    assert (
        client.post(
            f"/payroll/{payroll_id}/review", data=dict(version_id=str(version), action="approve")
        ).status_code
        == 409
    )
    assert b"Clock records changed" in client.get("/payroll").data


@pytest.mark.parametrize("condition", ["open", "approved", "imported", "voided", "manual_mismatch"])
def test_clock_preparation_refuses_unresolved_or_protected_records(client, app, setup, condition):
    day = setup["week"] - timedelta(days=7)
    make_punch(app, setup["employee_profile_id"], day)
    with app.app_context():
        employee = db.session.get(Employee, setup["employee_profile_id"])
        if condition == "open":
            db.session.add(
                Attendance(
                    employee_id=employee.id,
                    date=day + timedelta(days=1),
                    clock_in_at=instant(day + timedelta(days=1), 9),
                )
            )
        else:
            row = PayrollRecord.build(employee, day, 4 if condition == "manual_mismatch" else 8)
            if condition == "approved":
                row.status = "Approved"
            elif condition == "imported":
                row.legacy_import = True
            elif condition == "voided":
                row.status = "Voided"
            db.session.add(row)
        db.session.commit()
    login(client)
    page = payroll_preview(client, setup, day)
    assert prepare(client, setup, day, page).status_code == 409
    with app.app_context():
        assert not any(row.attendance_fingerprint for row in PayrollRecord.query.all())


def test_employee_clock_review_ignores_another_employee_and_cannot_prepare(client, app, setup):
    day = setup["week"] - timedelta(days=7)
    make_punch(app, setup["admin_employee_id"], day)
    make_punch(app, setup["employee_profile_id"], day, end=12)
    login(client, "employee_user")
    page = client.get(
        "/payroll/clock-hours",
        query_string=dict(employee_id=setup["admin_employee_id"], week=day.isoformat()),
    )
    assert b"3.00 recorded hours" in page.data and b"8.00 recorded hours" not in page.data
    assert (
        client.post(
            "/payroll/clock-hours", data=dict(employee_id=setup["admin_employee_id"])
        ).status_code
        == 403
    )


def test_punches_split_at_local_midnight_and_round_only_after_daily_sum(app, setup):
    week = setup["week"] - timedelta(days=7)
    with app.app_context():
        employee = db.session.get(Employee, setup["employee_profile_id"])
        start = instant(week, 23)
        row = Attendance(employee=employee, date=week, clock_in_at=start)
        row.complete_shift(instant(week + timedelta(days=1), 1))
        db.session.add(row)
        for offset in [0, 30]:
            start = instant(week + timedelta(days=2), 9, offset)
            row = Attendance(employee=employee, date=week + timedelta(days=2), clock_in_at=start)
            row.complete_shift(start + timedelta(minutes=7, seconds=30))
            db.session.add(row)
        db.session.commit()
        preview = time_preview(employee, week, ZoneInfo("America/New_York"), store_today())
        assert [item["actual"] for item in preview["days"][:3]] == [
            Decimal("1.00"),
            Decimal("1.00"),
            Decimal("0.25"),
        ]


def test_new_commands_require_csrf(client, app, setup):
    login(client)
    app.config["WTF_CSRF_ENABLED"] = True
    for path in [
        "/schedule/availability",
        "/schedule/publish",
        "/schedule/copy",
        "/payroll/clock-hours",
        "/schedule/coverage/1/claim",
        "/schedule/coverage/1/review",
    ]:
        assert client.post(path).status_code == 400


def test_coverage_lists_are_bounded_but_later_offers_remain_accessible(client, app, setup):
    with app.app_context():
        for offset in range(31):
            day = setup["week"] + timedelta(days=offset)
            row = ScheduledShift(
                employee_id=setup["employee_profile_id"],
                starts_at=instant(day, 9),
                ends_at=instant(day, 17),
            )
            db.session.add(row)
            db.session.flush()
            db.session.add(
                ShiftCoverage(
                    shift=row,
                    offered_by_id=setup["employee_profile_id"],
                    shift_version=row.version_id,
                )
            )
        db.session.commit()
    login(client, "employee_user")
    first, second = client.get("/schedule/coverage"), client.get("/schedule/coverage?page=2")
    assert first.data.count(b"coverage-card") == 30 and b"More offers" in first.data
    assert second.data.count(b"coverage-card") == 1 and b"Previous offers" in second.data


@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.PAYROLL])
def test_planning_commands_enforce_schedule_write_roles(client, app, setup, role):
    with app.app_context():
        db.session.get(User, setup["employee_id"]).role = role
        db.session.commit()
    login(client, "employee_user")
    for path in ["/schedule/publish", "/schedule/copy", "/schedule/coverage/1/review"]:
        assert client.post(path).status_code == 403


def test_manager_cannot_approve_their_own_coverage_transfer(client, app, setup):
    identity, offer_id = offered(app, setup)
    claimant = app.test_client()
    login(claimant, "coverage_user")
    claimant.post(f"/schedule/coverage/{offer_id}/claim", data=dict(version_id="1"))
    with app.app_context():
        db.session.get(User, setup["employee_id"]).role = Role.MANAGER
        db.session.commit()
    login(client, "employee_user")
    response = client.post(
        f"/schedule/coverage/{offer_id}/review", data=dict(version_id="2", action="approve")
    )
    assert response.status_code == 403 and b"Another manager" in response.data
    with app.app_context():
        assert db.session.get(ScheduledShift, identity).employee_id == setup["employee_profile_id"]


def test_decline_can_clear_an_archived_claimant_and_withdraw_keeps_shift(client, app, setup):
    identity, offer_id = offered(app, setup)
    claimant = app.test_client()
    login(claimant, "coverage_user")
    claimant.post(f"/schedule/coverage/{offer_id}/claim", data=dict(version_id="1"))
    with app.app_context():
        db.session.get(Employee, setup["coworker_id"]).archived = True
        db.session.commit()
    login(client)
    assert (
        client.post(
            f"/schedule/coverage/{offer_id}/review", data=dict(version_id="2", action="approve")
        ).status_code
        == 409
    )
    assert (
        client.post(
            f"/schedule/coverage/{offer_id}/review", data=dict(version_id="2", action="reject")
        ).status_code
        == 302
    )
    original = app.test_client()
    login(original, "employee_user")
    offer_page = original.get("/schedule/coverage")
    assert (
        original.post(
            f"/schedule/coverage/{offer_id}/cancel",
            data=dict(version_id=hidden(offer_page, "version_id")),
        ).status_code
        == 302
    )
    with app.app_context():
        assert db.session.get(ShiftCoverage, offer_id).status == "Cancelled"
        assert db.session.get(ScheduledShift, identity).employee_id == setup["employee_profile_id"]
        assert not db.session.get(ScheduledShift, identity).cancelled


def test_unchanged_availability_does_not_send_more_manager_notifications(client, app, setup):
    login(client, "employee_user")
    values = availability_values()
    assert client.post("/schedule/availability", data=values).status_code == 302
    values["version_id"] = "1"
    assert client.post("/schedule/availability", data=values).status_code == 302
    with app.app_context():
        assert Notification.query.filter_by(title="Availability updated").count() == 1


def test_availability_checks_both_dates_of_overnight_shift(client, app, setup):
    values = availability_values()
    values.update(
        mode_0="hours",
        start_0="20:00",
        end_0="00:00",
        mode_1="hours",
        start_1="00:00",
        end_1="08:00",
    )
    login(client, "employee_user")
    client.post("/schedule/availability", data=values)
    manager = app.test_client()
    login(manager)
    start, end = (
        setup["week"].isoformat() + "T21:00",
        (setup["week"] + timedelta(days=1)).isoformat() + "T07:00",
    )
    assert (
        manager.post(
            "/schedule",
            data=dict(employee_id=setup["employee_profile_id"], starts_at=start, ends_at=end),
        ).status_code
        == 302
    )
    response = manager.post(
        "/schedule",
        data=dict(
            employee_id=setup["employee_profile_id"],
            starts_at=(setup["week"] + timedelta(days=7)).isoformat() + "T21:00",
            ends_at=(setup["week"] + timedelta(days=8)).isoformat() + "T09:00",
        ),
    )
    assert response.status_code == 400 and b"Outside submitted availability" in response.data
