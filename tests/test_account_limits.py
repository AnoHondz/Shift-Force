import secrets
import time

import pyotp
import pytest
from conftest import TEST_PASSWORD, TestConfig

from app import create_app
from app.account_security import encrypt_secret
from app.extensions import db
from app.models import Employee, Role, User


@pytest.fixture()
def limited_app():
    class Config(TestConfig):
        RATELIMIT_ENABLED = True

    app = create_app(Config)
    with app.app_context():
        db.create_all()
    yield app
    with app.app_context():
        db.session.remove()
        db.drop_all()


def test_twenty_staff_can_sign_in_from_one_store_wifi(limited_app):
    with limited_app.app_context():
        for number in range(20):
            user = User(
                username=f"crew.{number}",
                role=Role.EMPLOYEE,
                employee=Employee(
                    first_name="Crew", last_name=str(number), email=f"crew{number}@example.com"
                ),
            )
            user.set_password(TEST_PASSWORD)
            db.session.add(user)
        db.session.commit()
    for number in range(20):
        client = limited_app.test_client()
        assert (
            client.post(
                "/login", data={"username": f"crew.{number}", "password": TEST_PASSWORD}
            ).status_code
            == 302
        )
        assert client.get("/schedule").status_code == 200


def test_login_target_limit_normalizes_case_and_keeps_other_users_available(limited_app):
    client = limited_app.test_client()
    for attempt in range(10):
        name = "missing" if attempt % 2 else "  MISSING  "
        assert client.post("/login", data={"username": name}).status_code == 200
    assert client.post("/login", data={"username": "Missing"}).status_code == 429
    assert client.post("/login", data={"username": "another"}).status_code == 200
    assert (
        client.post(
            "/login", data={"username": "missing"}, environ_base={"REMOTE_ADDR": "192.0.2.10"}
        ).status_code
        == 200
    )


def test_login_ip_ceiling_blocks_rotating_account_names(limited_app):
    client = limited_app.test_client()
    for attempt in range(100):
        assert client.post("/login", data={"username": f"missing.{attempt}"}).status_code == 200
    assert client.post("/login", data={"username": "missing.extra"}).status_code == 429


def test_mfa_attempts_are_limited_per_account_on_shared_wifi(limited_app):
    factors = []
    with limited_app.app_context():
        for number in range(20):
            secret = pyotp.random_base32()
            user = User(username=f"crew.{number}", role=Role.EMPLOYEE)
            user.set_password(TEST_PASSWORD)
            user.mfa_secret = encrypt_secret(secret)
            db.session.add(user)
            db.session.flush()
            factors.append((user.auth_token, pyotp.TOTP(secret).now()))
        db.session.commit()
    clients = []
    for token, code in factors:
        client = limited_app.test_client()
        with client.session_transaction() as state:
            state.update(pending_login_token=token, pending_login_at=time.time())
        assert client.post("/account/verify", data={"code": code}).status_code == 302
        clients.append(client)
    # A successful code counts toward the account limit; other staff stay available.
    blocked = clients[0]
    for _ in range(9):
        assert blocked.post("/account/verify", data={"code": "invalid"}).status_code == 200
    assert blocked.post("/account/verify", data={"code": "invalid"}).status_code == 429
    assert (
        clients[1].post("/account/verify", data={"code": secrets.token_hex(8)}).status_code == 200
    )


def test_bulk_registration_does_not_block_distinct_staff_on_shared_wifi(limited_app):
    client = limited_app.test_client()
    for number in range(20):
        assert (
            client.post(
                "/signup", data={"username": f"new.{number}", "full_name": "Incomplete"}
            ).status_code
            == 200
        )
    for _ in range(9):
        assert client.post("/signup", data={"username": "new.0"}).status_code == 200
    assert client.post("/signup", data={"username": "new.0"}).status_code == 429
