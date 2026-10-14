import hashlib
import json
import re
import secrets
import time
from datetime import timedelta

import pyotp
import pytest
from conftest import TEST_PASSWORD

from app.account_security import decrypt_secret
from app.extensions import db
from app.models import AccountReset, Notification, Role, User, utcnow


def login(client, username="admin_user", remember=False):
    return client.post(
        "/login",
        data={
            "username": username,
            "password": TEST_PASSWORD,
            "remember_me": "on" if remember else "",
        },
    )


def enroll(client, app, users):
    login(client)
    assert (
        client.post(
            "/account/security", data={"action": "begin", "current_password": TEST_PASSWORD}
        ).status_code
        == 302
    )
    with app.app_context():
        user = db.session.get(User, users["admin_id"])
        encrypted = user.mfa_pending_secret
        secret = decrypt_secret(encrypted)
        assert secret not in encrypted
    assert client.get("/account/authenticator.png").mimetype == "image/png"
    result = client.post(
        "/account/security",
        data={
            "action": "enable",
            "current_password": TEST_PASSWORD,
            "code": pyotp.TOTP(secret).now(),
        },
    )
    assert result.status_code == 200
    codes = re.findall(rb"\b[a-f0-9]{16}\b", result.data)
    assert len(codes) == 10
    with app.app_context():
        user = db.session.get(User, users["admin_id"])
        assert user.mfa_pending_secret is None and user.mfa_secret == encrypted
        assert all(code.decode() not in user.mfa_recovery_hashes for code in codes)
    return secret, [code.decode() for code in codes]


def test_mfa_enrollment_login_and_single_use_recovery(client, app, users):
    secret, codes = enroll(client, app, users)
    assert codes[0].encode() not in client.get("/account/security").data
    client.post("/logout")
    response = login(client, remember=True)
    assert response.location.endswith("/account/verify")
    assert client.get("/manage").status_code == 302
    assert (
        client.post("/account/verify", data={"code": pyotp.TOTP(secret).now()}).status_code == 200
    )
    assert (
        b"already used"
        in client.post("/account/verify", data={"code": pyotp.TOTP(secret).now()}).data
    )
    assert client.post("/account/verify", data={"code": codes[0]}).status_code == 302
    assert client.get("/manage").status_code == 200
    client.post("/logout")
    login(client)
    assert b"already used" in client.post("/account/verify", data={"code": codes[0]}).data
    assert client.post("/account/verify", data={"code": codes[1]}).status_code == 302


def test_privileged_enrollment_gate_and_api_denial(client, app, users):
    app.config["REQUIRE_PRIVILEGED_MFA"] = True
    login(client)
    assert client.get("/schedule").location.endswith("/account/security")
    assert client.get("/api/employees").status_code == 403
    assert client.get("/account/security").status_code == 200
    client.post("/logout")
    login(client, "employee_user")
    assert client.get("/schedule").status_code == 200


def test_pending_mfa_expires_and_tampered_identity_is_denied(client, app, users):
    enroll(client, app, users)
    client.post("/logout")
    login(client)
    with client.session_transaction() as session:
        session["pending_login_at"] = time.time() - 301
    assert client.get("/account/verify").location.endswith("/login")
    login(client)
    with client.session_transaction() as session:
        session["pending_login_token"] = secrets.token_urlsafe(32)
    assert client.get("/account/verify").location.endswith("/login")


def test_password_change_revokes_other_sessions_and_remember_cookie(client, app, users):
    other = app.test_client()
    login(client, "employee_user")
    login(other, "employee_user", remember=True)
    password = secrets.token_urlsafe(20)
    response = client.post(
        "/account/security",
        data={
            "action": "password",
            "current_password": TEST_PASSWORD,
            "password": password,
            "confirm_password": password,
        },
    )
    assert response.status_code == 302
    assert other.get("/schedule").status_code == 302
    assert client.get("/schedule").status_code == 200
    with other.session_transaction() as session:
        session.clear()
    assert other.get("/schedule").status_code == 302


@pytest.mark.parametrize("action", ["role", "reset", "revoke"])
def test_owner_actions_require_password_and_cannot_modify_self(client, app, users, action):
    login(client)
    data = {"user_id": users["employee_id"], "action": action, "role": "manager"}
    assert client.post("/account/staff", data=data).status_code == 403
    data.update(current_password=TEST_PASSWORD, user_id=users["admin_id"])
    assert client.post("/account/staff", data=data).status_code == 403
    with app.app_context():
        assert db.session.get(User, users["employee_id"]).role == Role.EMPLOYEE
        assert AccountReset.query.count() == 0


@pytest.mark.parametrize("role", ["admin", "executive", "owner", "bogus"])
def test_staff_form_cannot_grant_platform_or_owner_access(client, app, users, role):
    login(client)
    assert (
        client.post(
            "/account/staff",
            data={
                "user_id": users["employee_id"],
                "action": "role",
                "role": role,
                "current_password": TEST_PASSWORD,
            },
        ).status_code
        == 400
    )


def test_owner_promotes_manager_and_revokes_old_staff_session(client, app, users):
    employee = app.test_client()
    login(employee, "employee_user")
    login(client)
    assert (
        client.post(
            "/account/staff",
            data={
                "user_id": users["employee_id"],
                "action": "role",
                "role": "manager",
                "current_password": TEST_PASSWORD,
            },
        ).status_code
        == 302
    )
    assert employee.get("/schedule").status_code == 302
    login(employee, "employee_user")
    assert employee.get("/manage/punches").status_code == 200
    with app.app_context():
        assert (
            Notification.query.filter_by(
                user_id=users["employee_id"], title="Account access changed"
            ).count()
            == 1
        )


def reset_token(client, users):
    page = client.post(
        "/account/staff",
        data={
            "user_id": users["employee_id"],
            "action": "reset",
            "current_password": TEST_PASSWORD,
        },
    )
    assert page.status_code == 200
    return re.search(rb"token=([A-Za-z0-9_-]{43})", page.data).group(1).decode()


def test_reset_link_hash_expiry_single_use_and_revocation(client, app, users):
    login(client)
    first = reset_token(client, users)
    second = reset_token(client, users)
    recovery = app.test_client()
    assert recovery.get("/account/reset?token=" + first).status_code == 400
    with app.app_context():
        record = AccountReset.query.filter_by(
            token_hash=hashlib.sha256(second.encode()).hexdigest()
        ).one()
        assert second not in record.token_hash
    password = secrets.token_urlsafe(20)
    assert (
        recovery.post(
            "/account/reset",
            data={"token": second, "password": password, "confirm_password": password},
        ).status_code
        == 302
    )
    assert recovery.get("/account/reset?token=" + second).status_code == 400
    assert (
        recovery.post(
            "/login", data={"username": "employee_user", "password": password}
        ).status_code
        == 302
    )
    third = reset_token(client, users)
    with app.app_context():
        record = AccountReset.query.filter_by(
            token_hash=hashlib.sha256(third.encode()).hexdigest()
        ).one()
        record.expires_at = utcnow() - timedelta(seconds=1)
        db.session.commit()
    assert recovery.get("/account/reset?token=" + third).status_code == 400


def test_account_changes_need_csrf_and_recent_second_factor(client, app, users):
    enroll(client, app, users)
    with client.session_transaction() as session:
        session["mfa_verified_at"] = time.time() - 301
    data = {
        "user_id": users["employee_id"],
        "action": "role",
        "role": "manager",
        "current_password": TEST_PASSWORD,
    }
    assert client.post("/account/staff", data=data).status_code == 403
    app.config["WTF_CSRF_ENABLED"] = True
    assert client.post("/account/staff", data=data).status_code == 400


def test_mfa_never_stores_secret_or_recovery_codes_in_session(client, app, users):
    secret, codes = enroll(client, app, users)
    with client.session_transaction() as session:
        payload = json.dumps(dict(session))
        assert secret not in payload and all(code not in payload for code in codes)
    response = client.get("/account/security")
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"


@pytest.mark.parametrize("action", ["role", "revoke", "reset"])
def test_recovery_and_revocation_discard_unfinished_mfa(client, app, users, action):
    staff = app.test_client()
    login(staff, "employee_user")
    assert (
        staff.post(
            "/account/security", data={"action": "begin", "current_password": TEST_PASSWORD}
        ).status_code
        == 302
    )
    assert staff.get("/account/authenticator.png").status_code == 200
    login(client)
    if action == "reset":
        token = reset_token(client, users)
        recovery = app.test_client()
        password = secrets.token_urlsafe(20)
        assert (
            recovery.post(
                "/account/reset",
                data={"token": token, "password": password, "confirm_password": password},
            ).status_code
            == 302
        )
    else:
        assert (
            client.post(
                "/account/staff",
                data={
                    "user_id": users["employee_id"],
                    "action": action,
                    "role": "manager",
                    "current_password": TEST_PASSWORD,
                },
            ).status_code
            == 302
        )
    with app.app_context():
        assert db.session.get(User, users["employee_id"]).mfa_pending_secret is None
    assert staff.get("/account/authenticator.png").status_code == 302


def test_password_recovery_retains_enabled_mfa_and_cannot_skip_verification(client, app, users):
    staff = app.test_client()
    login(staff, "employee_user")
    staff.post("/account/security", data={"action": "begin", "current_password": TEST_PASSWORD})
    with app.app_context():
        encrypted = db.session.get(User, users["employee_id"]).mfa_pending_secret
        secret = decrypt_secret(encrypted)
    assert (
        staff.post(
            "/account/security",
            data={
                "action": "enable",
                "current_password": TEST_PASSWORD,
                "code": pyotp.TOTP(secret).now(),
            },
        ).status_code
        == 200
    )
    login(client)
    token = reset_token(client, users)
    recovery = app.test_client()
    password = secrets.token_urlsafe(20)
    assert (
        recovery.post(
            "/account/reset",
            data={"token": token, "password": password, "confirm_password": password},
        ).status_code
        == 302
    )
    with app.app_context():
        assert db.session.get(User, users["employee_id"]).mfa_secret == encrypted
    assert staff.get("/schedule").status_code == 302
    response = recovery.post("/login", data={"username": "employee_user", "password": password})
    assert response.location.endswith("/account/verify")
    assert recovery.get("/schedule").status_code == 302
