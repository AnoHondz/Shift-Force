import json
import secrets
import shutil
import sqlite3
import time
from contextlib import closing
from types import SimpleNamespace

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet
from flask_migrate import upgrade

from app import create_app
from app.extensions import db
from app.models import Employee, Role, Store, User
from scripts.backup_scheduler import backup_cycle, maintenance
from scripts.backup_store_bundle import create_bundle, key_bytes, restore_bundle


@pytest.fixture()
def store_bundle(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    password = secrets.token_urlsafe(20)
    app = create_app(
        dict(
            TESTING=True,
            SECRET_KEY=secrets.token_hex(32),
            DATA_ROOT=str(root),
            SQLALCHEMY_DATABASE_URI="sqlite:///" + (root / "hub.db").as_posix(),
            STORE_DATABASE_ROOT=str(root / "stores"),
            RATELIMIT_ENABLED=False,
            WTF_CSRF_ENABLED=False,
            WORKFORCE_TIMEZONE="America/New_York",
        )
    )
    with app.app_context():
        upgrade()
        owner = User(
            username="owner",
            role=Role.ADMIN,
            employee=Employee(first_name="Pilot", last_name="Owner", email="owner@example.invalid"),
        )
        owner.set_password(password)
        db.session.add(owner)
        db.session.commit()
    client = app.test_client()
    client.post("/login", data={"username": "owner", "password": password})
    for index in range(2):
        assert (
            client.post(
                "/stores",
                data={
                    "name": "Pilot",
                    "location": f"Location {index}",
                    "slug": f"12345{index}",
                },
            ).status_code
            == 302
        )
    yield app, client, root, password
    for child in app.extensions["stores"].apps.values():
        with child.app_context():
            db.session.remove()
            db.engine.dispose()
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


def rows(path):
    with closing(sqlite3.connect(path)) as connection:
        tables = [
            r[0]
            for r in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        return {
            table: connection.execute('SELECT * FROM "' + table + '" ORDER BY 1').fetchall()
            for table in tables
        }


def test_encrypted_bundle_restores_hub_and_all_stores_with_every_row(store_bundle, tmp_path):
    app, _, root, _ = store_bundle
    key = Fernet.generate_key().decode()
    archive = tmp_path / "backup.enc"
    before = rows(root / "hub.db")
    manifest = create_bundle(root / "hub.db", root / "stores", archive, key)
    assert len(manifest["files"]) == 3
    destination = tmp_path / "restored"
    assert restore_bundle(archive, destination, key) == manifest
    assert rows(destination / "hub.db") == before
    with app.app_context():
        for store in Store.query.all():
            path = store.database_key + ".db"
            assert rows(destination / "stores" / path) == rows(root / "stores" / path)
    assert rows(root / "hub.db") == before
    assert b"SQLite format" not in archive.read_bytes()


@pytest.mark.parametrize("mode", ["tamper", "wrong_key", "truncate"])
def test_failed_restore_cannot_create_or_modify_destination(store_bundle, tmp_path, mode):
    _, _, root, _ = store_bundle
    key = Fernet.generate_key().decode()
    archive = tmp_path / "backup.enc"
    create_bundle(root / "hub.db", root / "stores", archive, key)
    if mode == "tamper":
        data = bytearray(archive.read_bytes())
        data[-20] ^= 1
        archive.write_bytes(data)
    elif mode == "truncate":
        archive.write_bytes(archive.read_bytes()[:-32])
    else:
        key = Fernet.generate_key().decode()
    with pytest.raises((InvalidTag, ValueError)):
        restore_bundle(archive, tmp_path / "failed", key)
    assert not (tmp_path / "failed").exists()


def test_restore_and_backup_refuse_existing_destination(store_bundle, tmp_path):
    _, _, root, _ = store_bundle
    key = Fernet.generate_key().decode()
    archive = tmp_path / "backup.enc"
    create_bundle(root / "hub.db", root / "stores", archive, key)
    old = archive.read_bytes()
    with pytest.raises(ValueError):
        create_bundle(root / "hub.db", root / "stores", archive, key)
    assert archive.read_bytes() == old
    with pytest.raises(ValueError):
        restore_bundle(archive, root, key)


@pytest.mark.parametrize(
    "key", [None, "", "not-base64", "a" * 43, "a" * 43 + "!", "a" * 43 + "=junk"]
)
def test_invalid_backup_key_is_rejected_before_file_creation(key):
    with pytest.raises(ValueError):
        key_bytes(key)


def test_missing_registered_store_fails_without_successful_backup(store_bundle, tmp_path):
    app, _, root, _ = store_bundle
    with app.app_context():
        store = Store.query.first()
        store.database_key = secrets.token_hex(16)
        db.session.commit()
    with pytest.raises(FileNotFoundError):
        create_bundle(
            root / "hub.db",
            root / "stores",
            tmp_path / "missing.enc",
            Fernet.generate_key().decode(),
        )
    assert not (tmp_path / "missing.enc").exists()


class FakeBucket:
    def __init__(self, path, corrupt=False):
        self.path = path
        self.corrupt = corrupt

    def upload_file(self, source, bucket, key, ExtraArgs):
        assert ExtraArgs["ServerSideEncryption"] == "AES256"
        shutil.copyfile(source, self.path)

    def download_file(self, bucket, key, target):
        shutil.copyfile(self.path, target)
        if self.corrupt:
            with open(target, "ab") as file:
                file.write(b"damaged")


def test_backup_cycle_verifies_remote_copy_and_restore_before_health_stamp(store_bundle, tmp_path):
    _, _, root, _ = store_bundle
    config = dict(
        DATA_ROOT=str(root),
        HUB_DATABASE_PATH=str(root / "hub.db"),
        STORE_DATABASE_ROOT=str(root / "stores"),
        BACKUP_BUCKET="owned-test-bucket",
        BACKUP_ENCRYPTION_KEY=Fernet.generate_key().decode(),
    )
    result = backup_cycle(config, FakeBucket(tmp_path / "remote.enc"))
    assert result["databases"] == 3 and result["restore_verified_at"] <= time.time()
    previous = (root / "backups" / "status.json").read_text()
    with pytest.raises(ValueError):
        backup_cycle(config, FakeBucket(tmp_path / "damaged.enc", corrupt=True))
    assert (root / "backups" / "status.json").read_text() == previous
    assert not (root / "maintenance.lock").exists()


def test_health_readiness_and_maintenance_are_data_free(store_bundle):
    _, client, root, _ = store_bundle
    assert client.get("/healthz").json == {"status": "ok"}
    response = client.get("/readyz")
    assert response.status_code == 200 and response.json["status"] == "ready"
    assert response.headers["X-Request-ID"]
    (root / "maintenance.lock").write_text(json.dumps({"owner": "test"}))
    assert client.get("/readyz").status_code == 503
    assert (
        client.post("/stores", data={"name": "Blocked", "location": "Maintenance"}).status_code
        == 503
    )
    assert client.get("/healthz").status_code == 200


def test_store_owner_can_manage_access_with_hub_password(store_bundle):
    app, client, _, password = store_bundle
    client.post("/stores/123450/open")
    child = app.extensions["stores"].get_app("123450")
    with child.app_context():
        person = User(
            username="crew",
            role=Role.EMPLOYEE,
            employee=Employee(first_name="Crew", last_name="Member", email="crew@example.invalid"),
        )
        person.set_password(secrets.token_urlsafe(20))
        db.session.add(person)
        db.session.commit()
        person_id = person.id
    response = client.post(
        "/s/123450/account/staff",
        data={
            "user_id": person_id,
            "action": "role",
            "role": "manager",
            "current_password": password,
        },
    )
    assert response.status_code == 302
    with child.app_context():
        assert db.session.get(User, person_id).role == Role.MANAGER
    with app.app_context():
        owner = User.query.filter_by(username="owner").one()
        owner.revoke_sessions()
        db.session.commit()
    assert client.get("/s/123450/account/staff").status_code == 302


def production_readiness(app, root):
    app.config.update(APP_ENV="production", RATELIMIT_ENABLED=True)
    app.extensions["workforce_limiter"] = SimpleNamespace(
        storage=SimpleNamespace(check=lambda: True)
    )
    backup = root / "backups"
    backup.mkdir(exist_ok=True)
    return backup / "status.json"


@pytest.mark.parametrize("age", [0, 86400, 172799])
def test_production_ready_requires_recent_offhost_and_restore_checks(store_bundle, age):
    app, client, root, _ = store_bundle
    status = production_readiness(app, root)
    status.write_text(
        json.dumps(
            dict(offsite_verified_at=time.time() - age, restore_verified_at=time.time() - age)
        )
    )
    response = client.get("/readyz")
    assert response.status_code == 200 and all(response.json["checks"].values())
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("value", [None, "today", True, -1, float("inf"), float("nan")])
def test_invalid_backup_stamp_cannot_make_production_ready(store_bundle, value):
    app, client, root, _ = store_bundle
    status = production_readiness(app, root)
    status.write_text(json.dumps(dict(offsite_verified_at=value, restore_verified_at=time.time())))
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json["checks"] == dict(database=True, rate_limits=True, backups=False)


@pytest.mark.parametrize("age", [-3600, 172801])
def test_future_or_expired_backups_cannot_make_production_ready(store_bundle, age):
    app, client, root, _ = store_bundle
    status = production_readiness(app, root)
    status.write_text(
        json.dumps(dict(offsite_verified_at=time.time() - age, restore_verified_at=time.time()))
    )
    assert client.get("/readyz").status_code == 503


def test_dependency_failures_keep_other_readiness_results(store_bundle):
    app, client, root, _ = store_bundle
    status = production_readiness(app, root)
    status.write_text(
        json.dumps(dict(offsite_verified_at=time.time(), restore_verified_at=time.time()))
    )

    def unavailable():
        raise ConnectionError("Disposable storage unavailable")

    app.extensions["workforce_limiter"].storage.check = unavailable
    response = client.get("/readyz")
    assert response.status_code == 503
    assert response.json["checks"] == dict(database=True, rate_limits=False, backups=True)


def test_disabled_limits_are_never_production_ready(store_bundle):
    app, client, root, _ = store_bundle
    status = production_readiness(app, root)
    status.write_text(
        json.dumps(dict(offsite_verified_at=time.time(), restore_verified_at=time.time()))
    )
    app.config["RATELIMIT_ENABLED"] = False
    assert client.get("/readyz").json["checks"]["rate_limits"] is False


def test_ready_probe_never_creates_missing_registered_database(store_bundle):
    app, client, root, _ = store_bundle
    key = secrets.token_hex(16)
    with app.app_context():
        Store.query.first().database_key = key
        db.session.commit()
    assert client.get("/readyz").status_code == 503
    assert not (root / "stores" / (key + ".db")).exists()


def test_maintenance_removes_owned_marker_on_shutdown_and_preserves_foreign_marker(tmp_path):
    with pytest.raises(SystemExit):
        with maintenance(tmp_path):
            assert (tmp_path / "maintenance.lock").is_file()
            raise SystemExit(0)
    assert not (tmp_path / "maintenance.lock").exists()
    foreign = json.dumps(dict(owner="another-process"))
    (tmp_path / "maintenance.lock").write_text(foreign)
    with pytest.raises(FileExistsError):
        with maintenance(tmp_path):
            pytest.fail("Existing maintenance marker must never be overwritten")
    assert (tmp_path / "maintenance.lock").read_text() == foreign


@pytest.mark.parametrize(
    "origin",
    [
        "http://work.example",
        "https://user:password@work.example",
        "https://work.example?q=private",
        "https://work.example#private",
    ],
)
def test_production_rejects_unsafe_canonical_origin(tmp_path, origin):
    with pytest.raises(RuntimeError, match="canonical HTTPS"):
        create_app(
            dict(
                APP_ENV="production",
                SECRET_KEY=secrets.token_hex(32),
                SQLALCHEMY_DATABASE_URI="sqlite:///" + (tmp_path / "hub.db").as_posix(),
                DATA_ROOT=str(tmp_path),
                STORE_DATABASE_ROOT=str(tmp_path / "stores"),
                RATELIMIT_STORAGE_URI="redis://127.0.0.1:6379/15",
                SESSION_COOKIE_SECURE=True,
                REMEMBER_COOKIE_SECURE=True,
                MFA_ENCRYPTION_KEY=Fernet.generate_key().decode(),
                REQUIRE_PRIVILEGED_MFA=True,
                PUBLIC_ORIGIN=origin,
            )
        )


@pytest.mark.parametrize(
    "setting", ["RATELIMIT_ENABLED", "REQUIRE_PAYROLL_REVIEW", "REQUIRE_PRIVILEGED_MFA"]
)
def test_production_cannot_disable_mandatory_controls(tmp_path, setting):
    config = dict(
        APP_ENV="production",
        SECRET_KEY=secrets.token_hex(32),
        SQLALCHEMY_DATABASE_URI="sqlite:///" + (tmp_path / "hub.db").as_posix(),
        DATA_ROOT=str(tmp_path),
        STORE_DATABASE_ROOT=str(tmp_path / "stores"),
        RATELIMIT_STORAGE_URI="redis://127.0.0.1:6379/15",
        SESSION_COOKIE_SECURE=True,
        REMEMBER_COOKIE_SECURE=True,
        MFA_ENCRYPTION_KEY=Fernet.generate_key().decode(),
        REQUIRE_PRIVILEGED_MFA=True,
        REQUIRE_PAYROLL_REVIEW=True,
        PUBLIC_ORIGIN="https://work.example",
    )
    config[setting] = False
    with pytest.raises(RuntimeError, match="Production"):
        create_app(config)
