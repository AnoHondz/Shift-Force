import secrets

import pytest

from app import create_app
from app.extensions import db
from app.models import Employee, PayrollRecord, Role, User

TEST_PASSWORD = secrets.token_urlsafe(18)


class TestConfig:
    TESTING = True
    SECRET_KEY = secrets.token_hex(32)
    RATELIMIT_ENABLED = False
    RATELIMIT_STORAGE_URI = "memory://"
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SKIP_BOOTSTRAP_DATA = True
    WTF_CSRF_ENABLED = False


@pytest.fixture()
def app():
    app = create_app(TestConfig)
    with app.app_context():
        db.drop_all()
        db.create_all()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def users(app):
    with app.app_context():
        admin_employee = Employee(
            first_name="Admin",
            last_name="User",
            email="admin@example.com",
            job_title="Administrator",
            hourly_rate=60,
            overtime_multiplier=1.5,
        )
        employee_profile = Employee(
            first_name="Regular",
            last_name="Employee",
            email="employee@example.com",
            job_title="Engineer",
            hourly_rate=30,
            overtime_multiplier=1.5,
        )
        db.session.add_all([admin_employee, employee_profile])
        db.session.flush()

        admin = User(username="admin_user", role=Role.ADMIN, employee_id=admin_employee.id)
        admin.set_password(TEST_PASSWORD)
        employee = User(
            username="employee_user", role=Role.EMPLOYEE, employee_id=employee_profile.id
        )
        employee.set_password(TEST_PASSWORD)
        db.session.add_all([admin, employee])
        db.session.commit()

        return {
            "admin_id": admin.id,
            "employee_id": employee.id,
            "admin_employee_id": admin_employee.id,
            "employee_profile_id": employee_profile.id,
        }


@pytest.fixture()
def payroll_record(app, users):
    with app.app_context():
        employee = db.session.get(Employee, users["employee_profile_id"])
        record = PayrollRecord.build(employee, employee.date_hired, 9, "Test")
        db.session.add(record)
        db.session.commit()
        return record.id
