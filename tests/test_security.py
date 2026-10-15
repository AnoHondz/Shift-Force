import re
import secrets

import pytest
from conftest import TEST_PASSWORD

from app import create_app
from app.extensions import db
from app.models import Employee, Notification, Role, User


def login(client, username="employee_user", **kwargs):
    return client.post("/login", data={"username": username, "password": TEST_PASSWORD}, **kwargs)


def test_missing_secret_fails_closed():
    class Config:
        SECRET_KEY = None

    with pytest.raises(RuntimeError, match="FLASK_SECRET_KEY"):
        create_app(Config)


def test_production_rejects_debug_and_insecure_cookies():
    class Config:
        SECRET_KEY = secrets.token_hex(32)
        APP_ENV = "production"
        DEBUG = True

    with pytest.raises(RuntimeError, match="Production requires"):
        create_app(Config)


def test_registration_cannot_claim_existing_profile(client, app, users):
    response = client.post(
        "/signup",
        data={
            "full_name": "Someone Else",
            "username": "claim",
            "email": "employee@example.com",
            "password": TEST_PASSWORD,
            "confirm_password": TEST_PASSWORD,
        },
    )
    assert response.status_code == 200
    with app.app_context():
        assert User.query.filter_by(username="claim").first() is None
        assert Employee.query.count() == 2


def test_registration_ignores_elevated_role_and_codes(client, app):
    response = client.post(
        "/signup",
        data={
            "full_name": "Taylor Jordan",
            "username": "taylor",
            "email": "taylor@example.com",
            "password": TEST_PASSWORD,
            "confirm_password": TEST_PASSWORD,
            "role": Role.ADMIN,
            "admin_signup_code": secrets.token_hex(8),
        },
    )
    assert response.status_code == 302
    with app.app_context():
        user = User.query.filter_by(username="taylor").one()
        assert user.role == Role.EMPLOYEE
        assert user.password_hash != TEST_PASSWORD
        assert user.check_password(TEST_PASSWORD)


@pytest.mark.parametrize(
    "next_url",
    [
        "//example.com",
        "/\\example.com",
        "/%2fexample.com",
        "https://example.com",
        "/%0aevil",
        "///example.com",
    ],
)
def test_login_rejects_external_redirects(client, users, next_url):
    response = client.post(
        "/login",
        query_string={"next": next_url},
        data={"username": "employee_user", "password": TEST_PASSWORD},
    )
    assert response.headers["Location"] == "/employee/dashboard"


def test_csrf_requires_valid_token_for_login_and_logout(client, app, users):
    app.config["WTF_CSRF_ENABLED"] = True
    assert login(client).status_code == 400
    page = client.get("/login")
    token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
    assert (
        client.post(
            "/login",
            data={"csrf_token": token, "username": "employee_user", "password": TEST_PASSWORD},
        ).status_code
        == 302
    )
    assert client.get("/logout").status_code == 405
    assert client.post("/logout").status_code == 400
    page = client.get("/profile")
    token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
    assert client.post("/logout", data={"csrf_token": token}).status_code == 302
    assert client.get("/api/employees").status_code == 401


def test_notification_ownership_even_for_admin(client, app, users):
    with app.app_context():
        note = Notification(
            user_id=users["employee_id"],
            title="Private employee message",
            message="Personal details",
        )
        db.session.add(note)
        db.session.commit()
        note_id = note.id
    login(client, "admin_user")
    assert b"Private employee message" not in client.get("/notifications/").data
    client.post(f"/notifications/{note_id}/read")
    client.post("/notifications/mark-all")
    with app.app_context():
        assert not db.session.get(Notification, note_id).is_read


@pytest.mark.parametrize("path", ["/api/employees", "/api/payroll", "/api/attendance"])
def test_api_requires_login(client, path):
    response = client.get(path)
    assert response.status_code == 401
    assert response.is_json


def test_api_employee_scope(client, users):
    login(client)
    data = client.get("/api/employees?per_page=100000&page=-2").get_json()
    assert data["total"] == 1
    assert data["employees"][0]["id"] == users["employee_profile_id"]
    assert data["page"] == 1


@pytest.mark.parametrize("payload", [["bad"], {"question": 5}, {"question": "x" * 2001}])
def test_assistant_validates_json(client, users, payload):
    login(client)
    assert client.post("/assistant/api", json=payload).status_code == 400


def test_response_security_headers(client, users):
    login(client)
    response = client.get("/profile")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_registration_can_be_disabled(client, app):
    app.config["PUBLIC_REGISTRATION"] = False
    assert client.get("/signup").status_code == 403


def test_profile_cannot_change_compensation_role_or_schedule(client, app, users):
    login(client)
    client.post(
        "/profile",
        data={
            "first_name": "Regular",
            "last_name": "Employee",
            "email": "employee@example.com",
            "hourly_rate": "999",
            "role": "admin",
            "schedule": "Changed",
        },
    )
    with app.app_context():
        user = db.session.get(User, users["employee_id"])
        assert user.role == Role.EMPLOYEE
        assert user.employee.hourly_rate == 30
        assert user.employee.schedule != "Changed"
