from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from conftest import TEST_PASSWORD
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import Attendance, Employee, PayrollRecord, User
from app.payroll import PayPeriod, Workweek

MONDAY = date(2026, 10, 5)


@pytest.mark.parametrize(
    "hours,prior,regular,overtime,total",
    [
        (0, 0, 0, 0, "0.00"),
        (8, 0, 8, 0, "160.00"),
        (8, 32, 8, 0, "160.00"),
        (8, 36, 4, 4, "200.00"),
        (8, 40, 0, 8, "240.00"),
        (0.25, 39.9, 0.1, 0.15, "6.50"),
    ],
)
def test_overtime_decimal_boundaries(hours, prior, regular, overtime, total):
    r, ot, pay = PayrollRecord.calculate(hours, 20, 1.5, prior_hours=prior)
    assert r == Decimal(str(regular))
    assert ot == Decimal(str(overtime))
    assert pay == Decimal(total)
    assert isinstance(pay, Decimal)


@pytest.mark.parametrize("hours", [-1, 25, "NaN", "Infinity", "wrong", "0.001"])
def test_invalid_daily_hours(hours):
    with pytest.raises(ValueError):
        PayrollRecord.calculate(hours, 20, 1.5)


def test_half_up_monetary_rounding():
    assert PayrollRecord.calculate("0.50", "20.01", "1.50")[2] == Decimal("10.01")


def test_workweek_sunday_monday_and_partial_pay_period():
    assert Workweek.containing(MONDAY + timedelta(days=6)).start == MONDAY
    assert Workweek.containing(MONDAY + timedelta(days=7)).start == MONDAY + timedelta(days=7)
    assert PayPeriod(
        MONDAY + timedelta(days=2), MONDAY + timedelta(days=3)
    ).end == MONDAY + timedelta(days=3)
    with pytest.raises(ValueError):
        PayPeriod(MONDAY + timedelta(days=1), MONDAY)


def test_multiple_days_correction_recalculates_entire_week_and_resets_approvals(client, app, users):
    client.post("/login", data={"username": "employee_user", "password": TEST_PASSWORD})
    for offset in range(5):
        assert (
            client.post(
                "/payroll",
                data={
                    "work_date": (MONDAY + timedelta(days=offset)).isoformat(),
                    "hours_worked": "9",
                },
            ).status_code
            == 302
        )
    with app.app_context():
        rows = PayrollRecord.query.order_by(PayrollRecord.work_date).all()
        assert sum(row.hours_worked for row in rows) == 45
        assert sum(row.overtime_hours for row in rows) == 5
        assert sum(row.total_pay for row in rows) == Decimal("1425")
        for row in rows:
            row.status = "Approved"
            row.reviewed_by_id = users["admin_id"]
        db.session.commit()
    # Employees cannot change approved entries.
    assert (
        client.post(
            "/payroll", data={"work_date": MONDAY.isoformat(), "hours_worked": "4"}
        ).status_code
        == 403
    )
    client.post("/logout")
    client.post("/login", data={"username": "admin_user", "password": TEST_PASSWORD})
    client.post(
        "/payroll",
        data={
            "employee_id": users["employee_profile_id"],
            "work_date": MONDAY.isoformat(),
            "hours_worked": "4",
        },
    )
    with app.app_context():
        rows = PayrollRecord.query.order_by(PayrollRecord.work_date).all()
        assert len(rows) == 5
        assert sum(row.overtime_hours for row in rows) == 0
        assert sum(row.total_pay for row in rows) == 1200
        assert rows[-1].status == "Pending"
        assert rows[-1].reviewed_by_id is None
        assert rows[0].status == "Pending"


def test_backdated_shift_reallocates_overtime_and_next_week_resets(app, users):
    with app.app_context():
        employee = db.session.get(Employee, users["employee_profile_id"])
        for offset in [4, 3, 2, 1, 0, 7]:
            db.session.add(PayrollRecord.build(employee, MONDAY + timedelta(days=offset), 9))
        db.session.flush()
        PayrollRecord.recalculate_week(employee.id, MONDAY)
        PayrollRecord.recalculate_week(employee.id, MONDAY + timedelta(days=7))
        db.session.commit()
        rows = PayrollRecord.query.order_by(PayrollRecord.work_date).all()
        assert rows[4].overtime_hours == 5
        assert rows[5].overtime_hours == 0


def test_rate_snapshot_prevents_retroactive_compensation_changes(app, users):
    with app.app_context():
        employee = db.session.get(Employee, users["employee_profile_id"])
        record = PayrollRecord.build(employee, MONDAY, 8)
        db.session.add(record)
        db.session.commit()
        employee.hourly_rate = 50
        PayrollRecord.recalculate_week(employee.id, MONDAY)
        db.session.commit()
        assert record.total_pay == 240
        assert record.rate_snapshot == 30


def test_duplicate_daily_record_constraint(app, users):
    with app.app_context():
        employee = db.session.get(Employee, users["employee_profile_id"])
        db.session.add_all(
            [PayrollRecord.build(employee, MONDAY, 8), PayrollRecord.build(employee, MONDAY, 8)]
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_void_retains_record_and_recalculates_week(client, app, users):
    with app.app_context():
        employee = db.session.get(Employee, users["employee_profile_id"])
        rows = [PayrollRecord.build(employee, MONDAY + timedelta(days=n), 9) for n in range(5)]
        db.session.add_all(rows)
        db.session.flush()
        PayrollRecord.recalculate_week(employee.id, MONDAY)
        db.session.commit()
        record_id = rows[0].id
    client.post("/login", data={"username": "admin_user", "password": TEST_PASSWORD})
    assert client.post(f"/payroll/{record_id}/delete").status_code == 302
    with app.app_context():
        assert PayrollRecord.query.count() == 5
        assert db.session.get(PayrollRecord, record_id).status == "Voided"
        assert (
            sum(
                row.overtime_hours
                for row in PayrollRecord.query.filter(PayrollRecord.status != "Voided")
            )
            == 0
        )


def test_payroll_submission_cannot_target_other_employee(client, app, users):
    client.post("/login", data={"username": "employee_user", "password": TEST_PASSWORD})
    client.post(
        "/payroll",
        data={
            "employee_id": users["admin_employee_id"],
            "work_date": MONDAY.isoformat(),
            "hours_worked": "8",
        },
    )
    with app.app_context():
        assert PayrollRecord.query.one().employee_id == users["employee_profile_id"]


def test_payroll_self_approval_forbidden(client, app, users, payroll_record):
    with app.app_context():
        db.session.get(User, users["employee_id"]).role = "payroll"
        db.session.commit()
    client.post("/login", data={"username": "employee_user", "password": TEST_PASSWORD})
    assert (
        client.post(f"/payroll/{payroll_record}/review", data={"action": "approve"}).status_code
        == 403
    )


def test_partial_report_does_not_reset_weekly_overtime(client, app, users):
    with app.app_context():
        employee = db.session.get(Employee, users["employee_profile_id"])
        db.session.add_all(
            [PayrollRecord.build(employee, MONDAY + timedelta(days=n), 9) for n in range(5)]
        )
        db.session.flush()
        PayrollRecord.recalculate_week(employee.id, MONDAY)
        db.session.commit()
    client.post("/login", data={"username": "admin_user", "password": TEST_PASSWORD})
    friday = (MONDAY + timedelta(days=4)).isoformat()
    report = client.get(f"/reports/export.csv?start_date={friday}&end_date={friday}")
    assert b"5.00" in report.data
    assert b"345.00" in report.data


def test_timezone_normalized_attendance_and_invalid_ranges():
    start = datetime(2026, 10, 5, 10, tzinfo=timezone(timedelta(hours=-4)))
    shift = Attendance(clock_in_at=start)
    shift.complete_shift(datetime(2026, 10, 5, 16, tzinfo=timezone.utc))
    assert shift.hours_worked == 2
    assert shift.clock_out_at == datetime(2026, 10, 5, 16)
    with pytest.raises(ValueError, match="already completed"):
        shift.complete_shift(datetime(2026, 10, 5, 17))
    for end in [start, start - timedelta(hours=1), start + timedelta(hours=25)]:
        with pytest.raises(ValueError):
            Attendance(clock_in_at=start).complete_shift(end)
