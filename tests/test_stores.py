import re
import time
from datetime import timedelta

import pytest
from conftest import TEST_PASSWORD

from app.extensions import db
from app.models import Employee, Role, Store, TeamMessage, User
from app.stores import invite_serializer


@pytest.fixture()
def locations(app, client, users, tmp_path):
    app.config.update(
        STORE_DATABASE_ROOT=str(tmp_path / "stores"), REMEMBER_COOKIE_DURATION=timedelta(days=7)
    )
    client.post("/login", data={"username": "admin_user", "password": TEST_PASSWORD})
    for location, slug in [
        ("Broad Street", "mcdonalds-broad-street"),
        ("Cox Street", "mcdonalds-cox-street"),
    ]:
        response = client.post(
            "/stores",
            data={
                "name": "McDonald's",
                "location": location,
                "slug": slug,
                "timezone": "America/New_York",
            },
        )
        assert response.status_code == 302
    router = app.extensions["stores"]
    broad = router.get_app("mcdonalds-broad-street")
    cox = router.get_app("mcdonalds-cox-street")
    yield broad, cox
    for child in router.apps.values():
        with child.app_context():
            db.session.remove()
            db.engine.dispose()


def add_staff(child, name="Store Employee", username=None):
    with child.app_context():
        first, last = name.split(" ", 1)
        employee = Employee(
            first_name=first, last_name=last, email="crew@example.com", hourly_rate=30
        )
        db.session.add(employee)
        db.session.flush()
        if username:
            account = User(username=username, role=Role.EMPLOYEE, employee=employee)
            account.set_password(TEST_PASSWORD)
            db.session.add(account)
            db.session.flush()
            db.session.add(
                TeamMessage(user_id=account.id, channel_id=1, body=name + " private message")
            )
        db.session.commit()
        return employee.id


def test_planning_availability_and_clock_review_stay_inside_the_selected_store(
    app, client, locations
):
    from datetime import datetime, timezone
    from datetime import time as clock_time
    from zoneinfo import ZoneInfo

    from app.models import Attendance, AvailabilityProfile, ScheduledShift
    from app.scheduling import monday

    broad, cox = locations
    broad_id = add_staff(broad, "Broad Coworker", "crew")
    cox_id = add_staff(cox, "Cox Coworker", "crew")
    past = monday(datetime.now(timezone.utc).date()) - timedelta(days=7)
    future = past + timedelta(days=21)
    for child, identity, hours in [(broad, broad_id, 2), (cox, cox_id, 4)]:
        with child.app_context():
            start = (
                datetime.combine(past, clock_time(9), ZoneInfo("America/New_York"))
                .astimezone(timezone.utc)
                .replace(tzinfo=None)
            )
            punch = Attendance(employee_id=identity, date=past, clock_in_at=start)
            punch.complete_shift(start + timedelta(hours=hours))
            db.session.add(punch)
            db.session.commit()
    a, b = "/s/mcdonalds-broad-street", "/s/mcdonalds-cox-street"
    client.post("/stores/mcdonalds-broad-street/open")
    response = client.post(
        a + "/schedule",
        data=dict(
            employee_id=broad_id,
            starts_at=future.isoformat() + "T09:00",
            ends_at=future.isoformat() + "T17:00",
            publish="0",
            notes="Broad private draft",
        ),
    )
    assert response.status_code == 302 and response.location.startswith(a + "/schedule")
    preview = client.get(
        a + "/payroll/clock-hours", query_string=dict(employee_id=broad_id, week=past.isoformat())
    )
    assert b"2.00 recorded hours" in preview.data and b"4.00 recorded hours" not in preview.data
    assert (a + "/schedule/publish").encode() in client.get(
        a + "/schedule?week=" + future.isoformat()
    ).data
    client.post("/stores/mcdonalds-cox-street/open")
    assert b"Broad private draft" not in client.get(b + "/schedule?week=" + future.isoformat()).data
    staff = app.test_client()
    staff.post(a + "/login", data=dict(username="crew", password=TEST_PASSWORD))
    values = {f"mode_{number}": "any" for number in range(7)}
    values.update(version_id="0", mode_0="off")
    assert staff.post(a + "/schedule/availability", data=values).status_code == 302
    assert b"Broad private draft" not in staff.get(a + "/schedule?week=" + future.isoformat()).data
    assert staff.get(b + "/schedule/coverage").status_code == 302
    with broad.app_context():
        assert AvailabilityProfile.query.one().employee_id == broad_id
        assert not ScheduledShift.query.one().published
    with cox.app_context():
        assert AvailabilityProfile.query.count() == ScheduledShift.query.count() == 0


def test_owner_can_switch_between_private_locations(app, client, locations):
    broad, cox = locations
    add_staff(broad, "Broad Coworker", "crew")
    add_staff(cox, "Cox Coworker", "crew")
    assert broad.config["SQLALCHEMY_DATABASE_URI"] != cox.config["SQLALCHEMY_DATABASE_URI"]
    assert broad.secret_key != cox.secret_key != app.secret_key
    for slug, expected, absent in [
        ("mcdonalds-broad-street", b"Broad Coworker", b"Cox Coworker"),
        ("mcdonalds-cox-street", b"Cox Coworker", b"Broad Coworker"),
    ]:
        response = client.post(f"/stores/{slug}/open")
        assert response.status_code == 302 and response.location == f"/s/{slug}/dashboard"
        assert expected in client.get(f"/s/{slug}/employees").data
        assert absent not in client.get(f"/s/{slug}/employees").data
        messages = client.get(f"/s/{slug}/api/chat/messages").json["messages"]
        assert len(messages) == 1 and messages[0]["body"].startswith(expected.decode())
    with app.app_context():
        assert Employee.query.count() == 2 and TeamMessage.query.count() == 0


def test_hub_and_store_cookies_cannot_authenticate_other_locations(app, client, locations):
    broad, cox = locations
    add_staff(broad, username="crew")
    add_staff(cox, username="crew")
    a = "/s/mcdonalds-broad-street"
    b = "/s/mcdonalds-cox-street"
    assert client.get(a + "/api/chat/messages").status_code == 401
    response = client.post(
        a + "/login", data={"username": "crew", "password": TEST_PASSWORD, "remember_me": "on"}
    )
    assert response.location == a + "/employee/dashboard"
    cookie = client.get_cookie(broad.config["REMEMBER_COOKIE_NAME"], path=a)
    assert cookie.http_only and cookie.same_site == "Lax"
    assert broad.config["SESSION_COOKIE_PATH"] == a
    assert client.get(b + "/api/chat/messages").status_code == 401
    client.set_cookie(cox.config["REMEMBER_COOKIE_NAME"], cookie.value, path=b)
    assert client.get(b + "/api/chat/messages").status_code == 401
    client.set_cookie(cox.config["SESSION_COOKIE_NAME"], client.get_cookie("session").value, path=b)
    assert client.get(b + "/api/chat/messages").status_code == 401
    client.delete_cookie(broad.config["SESSION_COOKIE_NAME"], path=a)
    assert client.get(a + "/api/chat/messages").status_code == 200
    assert client.post(a + "/logout").status_code == 302
    assert client.get_cookie(broad.config["REMEMBER_COOKIE_NAME"], path=a) is None


def test_store_registration_requires_location_bound_invite(client, locations):
    broad, cox = locations
    employee_id = add_staff(broad)
    add_staff(cox)
    with broad.app_context():
        token = invite_serializer().dumps({"employee_id": employee_id})
    a = "/s/mcdonalds-broad-street"
    b = "/s/mcdonalds-cox-street"
    assert client.get(a + "/signup").status_code == 403
    assert client.get(b + "/signup?invite=" + token).status_code == 403
    assert client.get(a + "/signup?invite=" + token + "tampered").status_code == 403
    assert client.get(a + "/signup?invite=" + token).status_code == 200
    response = client.post(
        a + "/signup",
        data={
            "invite": token,
            "username": "new.crew",
            "password": TEST_PASSWORD,
            "confirm_password": TEST_PASSWORD,
            "full_name": "Different Person",
            "email": "different@example.com",
            "role": "admin",
        },
    )
    assert response.status_code == 302 and response.location == a + "/login"
    with broad.app_context():
        account = User.query.filter_by(username="new.crew").one()
        assert account.role == Role.EMPLOYEE and account.employee_id == employee_id
        assert account.employee.email == "crew@example.com"
        assert account.employee.full_name() == "Store Employee"
        assert Employee.query.count() == 2
    assert client.get(a + "/signup?invite=" + token).status_code == 403
    with cox.app_context():
        assert User.query.count() == 1


def test_store_assets_and_redirects_stay_under_location(client, locations):
    broad, _ = locations
    add_staff(broad, username="crew")
    prefix = "/s/mcdonalds-broad-street"
    response = client.get(prefix + "/chat")
    assert response.status_code == 302 and response.location.startswith(prefix + "/login")
    response = client.post(
        prefix + "/login?next=/chat", data={"username": "crew", "password": TEST_PASSWORD}
    )
    assert response.location == prefix + "/chat"
    page = client.get(prefix + "/chat")
    assert (prefix + "/api/chat/messages").encode() in page.data
    assert (prefix + "/static/js/team-chat.js").encode() in page.data
    assert client.get(prefix + "/static/data/emoji.json").status_code == 200
    client.post(prefix + "/logout")
    response = client.post(
        prefix + "/login?next=/s/mcdonalds-cox-street/chat",
        data={"username": "crew", "password": TEST_PASSWORD},
    )
    assert response.location == prefix + "/employee/dashboard"


def test_staff_cannot_create_stores_or_invite_colleagues(client, locations):
    broad, _ = locations
    employee_id = add_staff(broad, username="crew")
    prefix = "/s/mcdonalds-broad-street"
    client.post(prefix + "/login", data={"username": "crew", "password": TEST_PASSWORD})
    assert client.get(prefix + "/stores").status_code == 404
    assert client.post(prefix + f"/employees/{employee_id}/invite").status_code == 403
    client.post("/logout")
    client.post("/login", data={"username": "employee_user", "password": TEST_PASSWORD})
    assert client.get("/stores").status_code == 403
    assert client.post("/stores/mcdonalds-broad-street/open").status_code == 403


def test_nonowner_admin_cannot_open_someone_elses_store(app, client, locations):
    with app.app_context():
        other = User(username="other.owner", role=Role.ADMIN)
        other.set_password(TEST_PASSWORD)
        db.session.add(other)
        db.session.commit()
    client.post("/logout")
    client.post("/login", data={"username": "other.owner", "password": TEST_PASSWORD})
    assert b'<article class="surface-card store-card"' not in client.get("/stores").data
    assert client.post("/stores/mcdonalds-broad-street/open").status_code == 404


@pytest.mark.parametrize(
    "path", ["/s", "/s/", "/s/no-such-store/login", "/s/../employees", "/s/not.valid/login"]
)
def test_unknown_store_paths_do_not_fall_back_to_hub(client, path):
    assert client.get(path).status_code == 404


def test_find_store_exposes_only_location_labels(client, locations):
    response = client.get("/stores/find?q=McDonald%27s+Broad")
    assert b"Broad Street" in response.data and b"Cox Street" not in response.data
    assert b"crew@example.com" not in response.data and b"Hourly rate" not in response.data
    assert b"/s/mcdonalds-broad-street/login" in response.data
    assert b"/s/mcdonalds-broad-street/login" not in client.get("/stores/find?q=%25").data


@pytest.mark.parametrize(
    "fields",
    [
        {"slug": "../../other"},
        {"slug": "a"},
        {"name": ""},
        {"location": ""},
        {"timezone": "not-a-zone"},
        {"name": "a" * 101},
    ],
)
def test_invalid_store_creation_is_atomic(app, client, users, tmp_path, fields):
    app.config["STORE_DATABASE_ROOT"] = str(tmp_path / "stores")
    client.post("/login", data={"username": "admin_user", "password": TEST_PASSWORD})
    data = {
        "name": "Example store",
        "location": "Broad Street",
        "slug": "example-broad",
        "timezone": "America/New_York",
        **fields,
    }
    assert client.post("/stores", data=data).status_code == 400
    with app.app_context():
        assert Store.query.count() == 0
    assert not (tmp_path / "stores").exists()


def test_duplicate_code_preserves_existing_store(app, client, locations):
    broad, _ = locations
    path = app.extensions["stores"].database_path(broad.config["STORE_INFO"])
    original = path.read_bytes()
    response = client.post(
        "/stores",
        data={
            "name": "Another brand",
            "location": "Elsewhere",
            "slug": "mcdonalds-broad-street",
            "timezone": "UTC",
        },
    )
    assert response.status_code == 409 and path.read_bytes() == original
    with app.app_context():
        assert Store.query.count() == 2


def test_owner_entry_and_staff_invites_require_each_locations_csrf(app, client, locations):
    broad, _ = locations
    employee_id = add_staff(broad)
    app.config["WTF_CSRF_ENABLED"] = True
    broad.config["WTF_CSRF_ENABLED"] = True
    assert client.post("/stores/mcdonalds-broad-street/open").status_code == 400
    page = client.get("/stores")
    root_token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
    assert (
        client.post(
            "/stores/mcdonalds-broad-street/open", data={"csrf_token": root_token}
        ).status_code
        == 302
    )
    prefix = "/s/mcdonalds-broad-street"
    path = prefix + f"/employees/{employee_id}/invite"
    assert client.post(path, data={"csrf_token": root_token}).status_code == 400
    page = client.get(prefix + f"/employees/{employee_id}")
    token = re.search(rb'name="csrf_token" value="([^"]+)"', page.data).group(1).decode()
    assert client.post(path, data={"csrf_token": token}).status_code == 200


def test_expired_and_archived_invitations_cannot_create_accounts(client, locations, monkeypatch):
    from itsdangerous import TimestampSigner

    broad, _ = locations
    employee_id = add_staff(broad)
    with monkeypatch.context() as patched:
        patched.setattr(TimestampSigner, "get_timestamp", lambda self: int(time.time()) - 8 * 86400)
        with broad.app_context():
            expired = invite_serializer().dumps({"employee_id": employee_id})
    prefix = "/s/mcdonalds-broad-street"
    assert client.get(prefix + "/signup?invite=" + expired).status_code == 403
    with broad.app_context():
        active = invite_serializer().dumps({"employee_id": employee_id})
        db.session.get(Employee, employee_id).archived = True
        db.session.commit()
    assert client.get(prefix + "/signup?invite=" + active).status_code == 403


def test_automatic_store_code_and_location_timezone(app, client, users, tmp_path):
    app.config["STORE_DATABASE_ROOT"] = str(tmp_path / "stores")
    client.post("/login", data={"username": "admin_user", "password": TEST_PASSWORD})
    assert (
        client.post(
            "/stores",
            data={
                "name": "Chick-fil-A",
                "location": "Cox Street",
                "timezone": "America/Los_Angeles",
            },
        ).status_code
        == 302
    )
    child = app.extensions["stores"].get_app("chick-fil-a-cox-street")
    with child.app_context():
        from app.routes.workplace import parse_shift_time

        assert parse_shift_time("2026-01-15T09:00").isoformat() == "2026-01-15T17:00:00"
        db.session.remove()
        db.engine.dispose()


def test_provision_failure_does_not_publish_location(app, client, users, tmp_path, monkeypatch):
    app.config["STORE_DATABASE_ROOT"] = str(tmp_path / "stores")
    client.post("/login", data={"username": "admin_user", "password": TEST_PASSWORD})

    def fail(*args):
        raise RuntimeError("Simulated provisioning failure")

    monkeypatch.setattr(app.extensions["stores"], "provision", fail)
    with pytest.raises(RuntimeError, match="Simulated"):
        client.post("/stores", data={"name": "Example", "location": "Cox Street"})
    with app.app_context():
        assert Store.query.count() == 0
    assert client.get("/s/example-cox-street/login").status_code == 404


def test_starting_existing_store_does_not_migrate_or_seed(app, client, locations, monkeypatch):
    broad, _ = locations
    router = app.extensions["stores"]
    router.apps.clear()

    def forbidden(*args, **kwargs):
        raise AssertionError("Startup must not run migrations.")

    monkeypatch.setattr("app.stores.upgrade", forbidden)
    assert client.get("/s/mcdonalds-broad-street/login").status_code == 200
    with router.get_app("mcdonalds-broad-street").app_context():
        assert User.query.count() == 1


def test_missing_database_never_opens_an_empty_or_default_workplace(app, client, users):
    with app.app_context():
        db.session.add(
            Store(
                owner_id=users["admin_id"],
                name="Missing",
                location="No database",
                slug="missing-store",
                database_key="a" * 32,
                timezone="UTC",
                owner_account_id=1,
            )
        )
        db.session.commit()
    assert client.get("/s/missing-store/login").status_code == 404


def test_owner_signup_is_restricted_to_own_locations(app, client, locations):
    client.post("/logout")
    assert client.get("/owner/signup").status_code == 200
    response = client.post(
        "/owner/signup",
        data={
            "full_name": "Separate Owner",
            "username": "separate.owner",
            "email": "owner@example.com",
            "password": TEST_PASSWORD,
            "confirm_password": TEST_PASSWORD,
            "role": "executive",
        },
    )
    assert response.status_code == 302
    with app.app_context():
        assert User.query.filter_by(username="separate.owner").one().role == Role.OWNER
    response = client.post("/login", data={"username": "separate.owner", "password": TEST_PASSWORD})
    assert response.location == "/stores"
    assert b"/stores/mcdonalds-broad-street/open" not in client.get("/stores").data
    assert client.post("/stores/mcdonalds-broad-street/open").status_code == 404
    for path in [
        "/employees",
        "/team",
        "/chat",
        "/schedule",
        "/payroll",
        "/reports",
        "/audit",
        "/manage",
        "/api/employees",
        "/api/chat/messages",
    ]:
        assert client.get(path).status_code == 403, path
    assert client.get("/dashboard").location == "/stores"
    assert (
        client.post("/stores", data={"name": "Chick-fil-A", "location": "Broad Street"}).status_code
        == 302
    )
    assert client.post("/stores/chick-fil-a-broad-street/open").status_code == 302
    assert client.get("/s/chick-fil-a-broad-street/employees").status_code == 200
    assert client.get("/s/mcdonalds-broad-street/api/chat/messages").status_code == 401
    page = client.get("/stores").data
    assert b"My stores" in page and b"Team chat</a>" not in page


def test_owner_signup_can_be_disabled_and_never_runs_inside_store(app, client, locations):
    client.post("/logout")
    app.config["PUBLIC_STORE_OWNER_REGISTRATION"] = False
    assert client.get("/owner/signup").status_code == 403
    assert client.get("/s/mcdonalds-broad-street/owner/signup").status_code == 403


def test_store_login_limits_are_separate(app, client, locations):
    app.config["RATELIMIT_ENABLED"] = True
    router = app.extensions["stores"]
    for child in router.apps.values():
        with child.app_context():
            db.session.remove()
            db.engine.dispose()
    router.apps.clear()
    a = "/s/mcdonalds-broad-street/login"
    b = "/s/mcdonalds-cox-street/login"
    for _ in range(10):
        assert client.post(a, data={"username": "missing"}).status_code == 200
    assert client.post(a, data={"username": "missing"}).status_code == 429
    assert client.post(b, data={"username": "missing"}).status_code == 200


def test_schedule_builder_cannot_read_or_modify_other_location(client, locations):
    from app.models import ScheduledShift

    broad, cox = locations
    add_staff(broad, "Broad Coworker", "crew")
    add_staff(cox, "Cox Coworker", "crew")
    client.post("/stores/mcdonalds-broad-street/open")
    response = client.post(
        "/s/mcdonalds-broad-street/schedule",
        data={
            "employee_id": "2",
            "simple_schedule": "1",
            "week": "2026-10-19",
            "start_time": "09:00",
            "end_time": "17:00",
            "shift_days": ["0", "2"],
            "notes": "Broad-only planned coverage",
        },
    )
    assert response.status_code == 302
    assert response.location.startswith("/s/mcdonalds-broad-street/schedule?week=")
    client.post("/stores/mcdonalds-cox-street/open")
    page = client.get("/s/mcdonalds-cox-street/schedule?week=2026-10-19")
    assert b"Broad-only planned coverage" not in page.data and b"Broad Coworker" not in page.data
    with broad.app_context():
        assert ScheduledShift.query.count() == 2
    with cox.app_context():
        assert ScheduledShift.query.count() == 0
