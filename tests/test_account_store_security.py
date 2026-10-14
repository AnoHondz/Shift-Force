import re
import secrets

from conftest import TEST_PASSWORD
from flask_login import login_user
from test_stores import add_staff
from test_stores import locations as locations

from app.extensions import db
from app.models import User


def test_staff_recovery_link_is_private_to_its_store(client, app, locations):
    broad, cox = locations
    add_staff(broad, "Broad Coworker", "crew")
    add_staff(cox, "Cox Coworker", "crew")
    a = "/s/mcdonalds-broad-street"
    b = "/s/mcdonalds-cox-street"
    client.post("/stores/mcdonalds-broad-street/open")
    response = client.post(
        a + "/account/staff",
        data={"user_id": 2, "action": "reset", "current_password": TEST_PASSWORD},
    )
    assert response.status_code == 200
    token = re.search(rb"token=([A-Za-z0-9_-]{43})", response.data).group(1).decode()
    recovery = app.test_client()
    assert recovery.get(b + "/account/reset?token=" + token).status_code == 400
    assert recovery.get("/account/reset?token=" + token).status_code == 400
    password = secrets.token_urlsafe(20)
    assert (
        recovery.post(
            a + "/account/reset",
            data={"token": token, "password": password, "confirm_password": password},
        ).status_code
        == 302
    )
    with broad.app_context():
        assert db.session.get(User, 2).check_password(password)
    with cox.app_context():
        assert db.session.get(User, 2).check_password(TEST_PASSWORD)


def test_store_owner_security_redirect_keeps_the_request_hub_prefix(locations):
    broad, _ = locations
    with broad.test_request_context(
        "/account/security",
        environ_overrides={
            "SCRIPT_NAME": "/portal/s/mcdonalds-broad-street",
            "shiftspace.hub_prefix": "/portal",
        },
    ):
        login_user(db.session.get(User, 1))
        response = broad.view_functions["accounts.security"]()
        assert response.location == "/portal/account/security"
