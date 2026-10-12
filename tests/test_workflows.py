from datetime import date, timedelta

import pytest
from conftest import TEST_PASSWORD
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    Attendance,
    AuditLog,
    Employee,
    LeaveRequest,
    Notification,
    Role,
    User,
    utcnow,
)


def login(client, username="employee_user"):
    return client.post("/login", data={"username": username, "password": TEST_PASSWORD})


def employee_form(**updates):
    data = {
        "first_name": "Sam",
        "last_name": "Taylor",
        "email": "sam@example.com",
        "job_title": "Engineer",
        "hourly_rate": "25.50",
        "overtime_multiplier": "1.50",
    }
    return dict(data, **updates)


def test_employee_create_edit_search_profile_and_archive(client, app, users):
    login(client, "admin_user")
    assert client.post("/employees/new", data=employee_form()).status_code == 302
    with app.app_context():
        employee_id = Employee.query.filter_by(email="sam@example.com").one().id
    assert b"Sam Taylor" in client.get("/employees?search=Sam").data
    assert b"Sam Taylor" in client.get(f"/employees/{employee_id}").data
    client.post(f"/employees/{employee_id}/edit", data=employee_form(first_name="Samuel"))
    assert b"Samuel" in client.get(f"/employees/{employee_id}").data
    client.post(f"/employees/{employee_id}/delete")
    with app.app_context():
        assert db.session.get(Employee, employee_id).archived
        assert AuditLog.query.filter_by(action_type="employee_created").count() == 1
        assert AuditLog.query.filter_by(action_type="employee_deleted").count() == 1


@pytest.mark.parametrize(
    "updates",
    [
        {"email": "invalid"},
        {"hourly_rate": "NaN"},
        {"hourly_rate": "inf"},
        {"hourly_rate": "-1"},
        {"department_id": "999"},
        {"department_id": "wrong"},
        {"first_name": "x" * 121},
        {"overtime_multiplier": "0.5"},
    ],
)
def test_employee_validation(client, app, users, updates):
    login(client, "admin_user")
    response = client.post("/employees/new", data=employee_form(**updates))
    assert response.status_code == 200
    with app.app_context():
        assert Employee.query.count() == 2


def test_employee_cannot_view_or_edit_other_profile(client, users):
    login(client)
    assert client.get(f"/employees/{users['admin_employee_id']}").status_code == 302
    assert (
        client.post(
            f"/employees/{users['admin_employee_id']}/edit", data=employee_form()
        ).status_code
        == 302
    )


def test_archiving_preserves_history_and_revokes_login(client, app, users, payroll_record):
    login(client, "admin_user")
    assert client.post(f"/employees/{users['employee_profile_id']}/delete").status_code == 302
    with app.app_context():
        employee = db.session.get(Employee, users["employee_profile_id"])
        assert len(employee.payroll_records) == 1
        assert employee.user is not None
    client.post("/logout")
    assert login(client).status_code == 200
    assert client.get("/api/employees").status_code == 401


def test_attendance_clock_in_out_duplicate_and_scope(client, app, users):
    login(client)
    assert (
        client.post(
            "/attendance/", data={"action": "clock_in", "employee_id": users["admin_employee_id"]}
        ).status_code
        == 302
    )
    client.post("/attendance/", data={"action": "clock_in"})
    with app.app_context():
        assert Attendance.query.count() == 1
        shift = Attendance.query.one()
        assert shift.employee_id == users["employee_profile_id"]
        shift.clock_in_at = utcnow() - timedelta(hours=2)
        db.session.commit()
    client.post("/attendance/", data={"action": "clock_out"})
    with app.app_context():
        shift = Attendance.query.one()
        assert shift.hours_worked == 2
        assert shift.status == "Completed"
    assert len(client.get("/api/attendance").get_json()["attendance_records"]) == 1


def test_attendance_active_unique_constraint(app, users):
    with app.app_context():
        for _ in range(2):
            db.session.add(
                Attendance(employee_id=users["employee_profile_id"], clock_in_at=utcnow())
            )
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_leave_validation_overlap_review_and_notifications(client, app, users):
    login(client)
    today = date.today()
    data = {
        "request_type": "Vacation",
        "start_date": today.isoformat(),
        "end_date": (today + timedelta(days=2)).isoformat(),
    }
    client.post("/leave/new", data=dict(data, end_date=(today - timedelta(days=1)).isoformat()))
    with app.app_context():
        assert LeaveRequest.query.count() == 0
    client.post("/leave/new", data=data)
    response = client.post("/leave/new", data=data)
    assert b"overlaps" in response.data
    with app.app_context():
        leave_id = LeaveRequest.query.one().id
        assert Notification.query.filter_by(user_id=users["admin_id"]).count() == 1
    assert client.post(f"/leave/{leave_id}/review", data={"action": "approve"}).status_code == 302
    client.post("/logout")
    login(client, "admin_user")
    assert client.post(f"/leave/{leave_id}/review", data={"action": "approve"}).status_code == 302
    assert client.post(f"/leave/{leave_id}/review", data={"action": "reject"}).status_code == 409
    with app.app_context():
        assert db.session.get(LeaveRequest, leave_id).status == "Approved"
        assert Notification.query.filter_by(user_id=users["employee_id"]).count() == 1
        assert AuditLog.query.filter_by(action_type="leave_reviewed").count() == 1


def test_no_self_approval(client, app, users):
    login(client, "admin_user")
    with app.app_context():
        leave = LeaveRequest(
            employee_id=users["admin_employee_id"],
            request_type="Vacation",
            start_date=date.today(),
            end_date=date.today(),
        )
        db.session.add(leave)
        db.session.commit()
        leave_id = leave.id
    assert client.post(f"/leave/{leave_id}/review", data={"action": "approve"}).status_code == 403


@pytest.mark.parametrize(
    "role,people_allowed,payroll_allowed,leave_allowed",
    [
        (Role.HR, True, False, True),
        (Role.PAYROLL, False, True, False),
        (Role.MANAGER, False, True, True),
        (Role.EMPLOYEE, False, False, False),
    ],
)
def test_role_mutation_permissions(
    client, app, users, payroll_record, role, people_allowed, payroll_allowed, leave_allowed
):
    with app.app_context():
        db.session.get(User, users["admin_id"]).role = role
        leave = LeaveRequest(
            employee_id=users["employee_profile_id"],
            request_type="Vacation",
            start_date=date.today(),
            end_date=date.today(),
        )
        db.session.add(leave)
        db.session.commit()
        leave_id = leave.id
    login(client, "admin_user")
    response = client.get("/employees/new")
    assert (response.status_code == 200) == people_allowed
    client.post(f"/payroll/{payroll_record}/review", data={"action": "approve"})
    client.post(f"/leave/{leave_id}/review", data={"action": "approve"})
    with app.app_context():
        from app.models import PayrollRecord

        assert (
            db.session.get(PayrollRecord, payroll_record).status == "Approved"
        ) == payroll_allowed
        assert (db.session.get(LeaveRequest, leave_id).status == "Approved") == leave_allowed


@pytest.mark.parametrize(
    "path",
    [
        "/dashboard",
        "/employees",
        "/profile",
        "/payroll",
        "/attendance/",
        "/leave/",
        "/leave/new",
        "/notifications/",
        "/reports/",
        "/audit/logs",
        "/settings",
        "/team",
    ],
)
def test_management_pages_render_with_empty_operational_data(client, users, path):
    login(client, "admin_user")
    assert client.get(path).status_code == 200
