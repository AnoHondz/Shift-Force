import secrets
from datetime import date, timedelta

import pytest
from conftest import TestConfig
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from app import create_app
from app.extensions import db
from app.models import Employee, LeaveRequest, PayrollRecord, Role, User


def test_concurrent_leave_overlap_database_constraint(app, users):
    with app.app_context():
        for offset in [0, 1]:
            db.session.add(
                LeaveRequest(
                    employee_id=users["employee_profile_id"],
                    request_type="Vacation",
                    start_date=date.today() + timedelta(days=offset),
                    end_date=date.today() + timedelta(days=2),
                )
            )
            if offset == 0:
                db.session.commit()
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()
        assert LeaveRequest.query.count() == 1


def test_stale_payroll_approval_cannot_overwrite_correction(app, users, payroll_record):
    with app.app_context():
        with Session(db.engine) as reviewer, Session(db.engine) as editor:
            reviewed = reviewer.get(PayrollRecord, payroll_record)
            corrected = editor.get(PayrollRecord, payroll_record)
            corrected.hours_worked = 8
            editor.commit()
            reviewed.status = "Approved"
            with pytest.raises(StaleDataError):
                reviewer.commit()


def test_invalid_foreign_key_is_rejected(app):
    with app.app_context():
        db.session.add(
            Employee(
                first_name="Invalid",
                last_name="Reference",
                email="invalid@example.com",
                department_id=999,
            )
        )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_create_admin_cli_and_explicit_role_assignment(app):
    password = secrets.token_urlsafe(20)
    runner = app.test_cli_runner()
    result = runner.invoke(
        args=["create-admin", "--username", "local.admin", "--email", "admin@example.com"],
        input=f"{password}\n{password}\n",
    )
    assert result.exit_code == 0, result.output
    assert runner.invoke(args=["set-role", "local.admin", "hr"]).exit_code == 0
    with app.app_context():
        user = User.query.filter_by(username="local.admin").one()
        assert user.role == Role.HR
        assert user.check_password(password)
    new_password = secrets.token_urlsafe(20)
    assert (
        runner.invoke(
            args=["reset-password", "local.admin"], input=f"{new_password}\n{new_password}\n"
        ).exit_code
        == 0
    )
    with app.app_context():
        assert User.query.one().check_password(new_password)


def test_demo_never_overwrites_existing_data(app, users):
    result = app.test_cli_runner().invoke(args=["seed-demo"])
    assert result.exit_code != 0
    with app.app_context():
        assert User.query.count() == 2


def test_demo_is_disabled_in_production(app):
    app.config["APP_ENV"] = "production"
    assert app.test_cli_runner().invoke(args=["seed-demo"]).exit_code != 0


def test_login_rate_limit_is_enforced():
    class Config(TestConfig):
        RATELIMIT_ENABLED = True

    app = create_app(Config)
    with app.app_context():
        db.create_all()
    client = app.test_client()
    for _ in range(10):
        assert (
            client.post(
                "/login", data={"username": "missing", "password": secrets.token_urlsafe(10)}
            ).status_code
            == 200
        )
    assert (
        client.post(
            "/login", data={"username": "missing", "password": secrets.token_urlsafe(10)}
        ).status_code
        == 429
    )


def test_csv_formula_text_is_neutralized(client, app, users, payroll_record):
    from conftest import TEST_PASSWORD

    with app.app_context():
        employee = db.session.get(Employee, users["employee_profile_id"])
        employee.first_name = "=SUM(1,1)"
        employee.job_title = "+Formula"
        db.session.commit()
    client.post("/login", data={"username": "admin_user", "password": TEST_PASSWORD})
    report = client.get("/reports/export.csv")
    assert b"'=SUM" in report.data
    assert b"'+Formula" in report.data


def test_attendance_correction_cli_requires_offsets_and_preserves_audit(app, users):
    from app.models import Attendance, AuditLog

    with app.app_context():
        shift = Attendance(
            employee_id=users["employee_profile_id"],
            clock_in_at=__import__("datetime").datetime(2026, 10, 5, 14),
        )
        db.session.add(shift)
        db.session.commit()
        shift_id = shift.id
    runner = app.test_cli_runner()
    assert (
        runner.invoke(
            args=[
                "correct-attendance",
                str(shift_id),
                "--clock-in",
                "2026-10-05T10:00:00",
                "--clock-out",
                "2026-10-05T12:00:00",
            ]
        ).exit_code
        != 0
    )
    assert (
        runner.invoke(
            args=[
                "correct-attendance",
                str(shift_id),
                "--clock-in",
                "2026-10-05T10:00:00-04:00",
                "--clock-out",
                "2026-10-05T12:00:00-04:00",
            ]
        ).exit_code
        == 0
    )
    with app.app_context():
        assert db.session.get(Attendance, shift_id).hours_worked == 2
        assert AuditLog.query.filter_by(action_type="attendance_corrected").count() == 1
