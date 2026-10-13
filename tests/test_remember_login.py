from datetime import datetime, timedelta, timezone

import pytest
from conftest import TEST_PASSWORD

from app.extensions import db
from app.models import Employee
from config import Config


@pytest.fixture(autouse=True)
def cookie_settings(app):
    app.config.update(
        REMEMBER_COOKIE_DURATION=Config.REMEMBER_COOKIE_DURATION,
        REMEMBER_COOKIE_HTTPONLY=Config.REMEMBER_COOKIE_HTTPONLY,
        REMEMBER_COOKIE_SAMESITE=Config.REMEMBER_COOKIE_SAMESITE,
        PERMANENT_SESSION_LIFETIME=Config.PERMANENT_SESSION_LIFETIME,
    )


def signin(client, remember=False, password=TEST_PASSWORD):
    return client.post(
        "/login",
        data={
            "username": "employee_user",
            "password": password,
            **({"remember_me": "on"} if remember else {}),
        },
    )


def test_remember_cookie_restores_session_and_logout_removes_it(client, users):
    assert signin(client, remember=True).status_code == 302
    cookie = client.get_cookie("remember_token")
    assert cookie is not None and cookie.http_only and cookie.same_site == "Lax"
    assert (
        timedelta(days=7) - timedelta(seconds=5)
        < (cookie.expires - datetime.now(timezone.utc))
        <= timedelta(days=7)
    )
    client.delete_cookie("session")
    assert client.get("/employee/dashboard").status_code == 200
    assert client.post("/logout").status_code == 302
    assert client.get_cookie("remember_token") is None
    client.delete_cookie("session")
    assert client.get("/employee/dashboard").status_code == 302


def test_without_remember_uses_a_browser_session(client, users):
    assert signin(client).status_code == 302
    assert client.get_cookie("remember_token") is None
    assert client.get_cookie("session").expires is None
    client.delete_cookie("session")
    assert client.get("/employee/dashboard").status_code == 302


def test_failed_signin_does_not_issue_remember_cookie(client, users):
    response = signin(client, remember=True, password="incorrect")
    assert response.status_code == 200
    assert client.get_cookie("remember_token") is None
    assert b'aria-describedby="remember-help" checked' in response.data
    assert client.get("/employee/dashboard").status_code == 302


def test_tampered_remember_cookie_does_not_authenticate(client, users):
    signin(client, remember=True)
    cookie = client.get_cookie("remember_token")
    client.delete_cookie("session")
    client.set_cookie("remember_token", cookie.value + "tampered")
    assert client.get("/employee/dashboard").status_code == 302


def test_archived_employee_cannot_return_with_remember_cookie(client, app, users):
    signin(client, remember=True)
    client.delete_cookie("session")
    with app.app_context():
        db.session.get(Employee, users["employee_profile_id"]).archived = True
        db.session.commit()
    assert client.get("/employee/dashboard").status_code == 302


def test_remember_cookie_uses_secure_flag_over_https(client, app, users):
    app.config.update(REMEMBER_COOKIE_SECURE=True, SESSION_COOKIE_SECURE=True)
    response = client.post(
        "/login",
        base_url="https://localhost",
        data={"username": "employee_user", "password": TEST_PASSWORD, "remember_me": "on"},
    )
    assert response.status_code == 302
    assert client.get_cookie("remember_token").secure
    assert client.get_cookie("session").secure
