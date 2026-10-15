import re
from datetime import timedelta

import pytest
from conftest import TEST_PASSWORD

from app.extensions import db
from app.models import Attendance, AuditLog, Role, User, utcnow


def login(client):
    return client.post("/login", data={"username": "admin_user", "password": TEST_PASSWORD})


@pytest.mark.parametrize("role", Role.WORKPLACE_CHOICES)
def test_dashboard_shift_clock_is_own_scoped_for_every_role(client, app, users, role):
    with app.app_context():
        db.session.get(User, users["admin_id"]).role = role
        db.session.commit()
    login(client)
    dashboard = "/employee/dashboard" if role == Role.EMPLOYEE else "/dashboard"
    page = client.get(dashboard)
    assert b"Your shift" in page.data and b"Off shift" in page.data
    response = client.post(
        "/attendance/shift",
        data={"action": "clock_in", "employee_id": users["employee_profile_id"]},
    )
    assert response.location.endswith(dashboard)
    assert b"On shift" in client.get(dashboard).data
    assert b"Clock out" in client.get(dashboard).data
    client.post("/attendance/shift", data={"action": "clock_in"})
    with app.app_context():
        shift = Attendance.query.one()
        assert shift.employee_id == users["admin_employee_id"]
        shift.clock_in_at = utcnow() - timedelta(hours=2, minutes=15)
        db.session.commit()
    response = client.post("/attendance/shift", data={"action": "clock_out"})
    assert response.location.endswith(dashboard)
    assert b"Off shift" in client.get(dashboard).data
    with app.app_context():
        shift = Attendance.query.one()
        assert shift.status == "Completed" and shift.hours_worked == 2.25
        assert AuditLog.query.filter_by(action_type="attendance_clocked_in").count() == 1
        assert AuditLog.query.filter_by(action_type="attendance_clocked_out").count() == 1


def test_dashboard_shift_status_does_not_use_another_employee(client, app, users):
    with app.app_context():
        db.session.add(Attendance(employee_id=users["employee_profile_id"], clock_in_at=utcnow()))
        db.session.commit()
    login(client)
    page = client.get("/dashboard")
    assert b"Off shift" in page.data and b">On shift<" not in page.data
    client.post("/attendance/shift", data={"action": "clock_out"})
    with app.app_context():
        assert Attendance.query.one().clock_out_at is None


def test_shift_clock_requires_authentication_profile_and_valid_action(client, app, users):
    assert client.post("/attendance/shift", data={"action": "clock_in"}).status_code == 302
    login(client)
    assert client.get("/attendance/shift").status_code == 405
    assert client.post("/attendance/shift", data={"action": "delete"}).status_code == 400
    with app.app_context():
        db.session.get(User, users["admin_id"]).employee_id = None
        db.session.commit()
    assert client.post("/attendance/shift", data={"action": "clock_in"}).status_code == 403
    with app.app_context():
        assert Attendance.query.count() == 0


def test_dashboard_shift_clock_requires_csrf(client, app, users):
    login(client)
    app.config["WTF_CSRF_ENABLED"] = True
    assert client.post("/attendance/shift", data={"action": "clock_in"}).status_code == 400
    page = client.get("/dashboard")
    token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
    assert (
        client.post(
            "/attendance/shift", data={"action": "clock_in", "csrf_token": token}
        ).status_code
        == 302
    )
    with app.app_context():
        assert Attendance.query.one().employee_id == users["admin_employee_id"]
