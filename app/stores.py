"""Dispatch store URLs to separate Flask apps, databases, keys and cookies."""

import hashlib
import hmac
import re
import secrets
from datetime import timedelta
from pathlib import Path
from threading import RLock

from flask import current_app, request
from flask_migrate import upgrade
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.exceptions import NotFound

from .extensions import db
from .models import Employee, Role, Store, User

STORE_CODE = re.compile(r"[a-z0-9][a-z0-9-]{2,59}")
MIGRATION_LOCK = RLock()


def hub_url(path):
    prefix = request.environ.get("shiftspace.hub_prefix", request.script_root)
    return prefix + path


def store_snapshot(store):
    return {
        key: getattr(store, key)
        for key in (
            "id",
            "owner_id",
            "slug",
            "name",
            "location",
            "timezone",
            "database_key",
            "owner_account_id",
        )
    }


def invite_serializer():
    return URLSafeTimedSerializer(current_app.secret_key, salt="shiftspace-store-invite-v1")


def invited_employee(token):
    if not current_app.config.get("STORE_SLUG") or not token or len(token) > 1000:
        return None
    try:
        payload = invite_serializer().loads(token, max_age=7 * 24 * 3600)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(payload, dict) or type(payload.get("employee_id")) is not int:
        return None
    employee = db.session.get(Employee, payload["employee_id"])
    return employee if employee and not employee.archived and not employee.user else None


class StoreRouter:
    def __init__(self, app, fallback):
        self.hub = app
        self.fallback = fallback
        self.apps = {}
        self.lock = RLock()

    def database_path(self, info):
        key = info["database_key"]
        if not re.fullmatch(r"[a-f0-9]{32}", key):
            raise RuntimeError("Invalid store storage key.")
        root = Path(
            self.hub.config.get("STORE_DATABASE_ROOT") or Path(self.hub.instance_path) / "stores"
        ).resolve()
        path = (root / f"{key}.db").resolve()
        if path.parent != root:
            raise RuntimeError("Invalid store database path.")
        return path

    def build(self, info, provisioning=False):
        from . import create_app

        path = self.database_path(info)
        if not provisioning and not path.is_file():
            raise NotFound()
        settings = dict(self.hub.config)
        prefix = self.hub.config.get("APPLICATION_ROOT", "/").rstrip("/") + "/s/" + info["slug"]
        settings.update(
            STORE_SLUG=info["slug"],
            STORE_INFO=info,
            SECRET_KEY=hmac.new(
                self.hub.secret_key.encode(), info["database_key"].encode(), hashlib.sha256
            ).hexdigest(),
            SQLALCHEMY_DATABASE_URI="sqlite:///" + path.as_posix(),
            SESSION_COOKIE_NAME="shiftspace_" + info["database_key"] + "_session",
            REMEMBER_COOKIE_NAME="shiftspace_" + info["database_key"] + "_remember",
            SESSION_COOKIE_PATH=prefix,
            REMEMBER_COOKIE_PATH=prefix,
            APPLICATION_ROOT=prefix,
            PUBLIC_REGISTRATION=False,
            WORKFORCE_TIMEZONE=info["timezone"],
            SESSION_COOKIE_HTTPONLY=True,
            REMEMBER_COOKIE_HTTPONLY=True,
            SESSION_COOKIE_SAMESITE=self.hub.config.get("SESSION_COOKIE_SAMESITE") or "Lax",
            REMEMBER_COOKIE_SAMESITE=self.hub.config.get("REMEMBER_COOKIE_SAMESITE") or "Lax",
            SESSION_COOKIE_DOMAIN=None,
            REMEMBER_COOKIE_DOMAIN=None,
            REMEMBER_COOKIE_DURATION=self.hub.config.get(
                "REMEMBER_COOKIE_DURATION", timedelta(days=7)
            ),
        )
        child = create_app(settings)
        child.extensions["store_hub"] = self.hub
        if not provisioning:
            with child.app_context():
                # Read only: starting an existing store never upgrades or seeds it.
                if not User.query.filter_by(id=info["owner_account_id"]).first():
                    db.engine.dispose()
                    raise NotFound()
        return child

    def provision(self, info, owner):
        path = self.database_path(info)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Reserve a fresh file; never replace an existing location.
        with path.open("xb"):
            pass
        child = self.build(info, provisioning=True)
        try:
            with MIGRATION_LOCK, child.app_context():
                upgrade(directory=str(Path(__file__).resolve().parents[1] / "migrations"))
                employee = Employee(
                    first_name=owner["first_name"],
                    last_name=owner["last_name"],
                    email=owner["email"],
                    job_title="Owner",
                    hourly_rate=0,
                )
                account = User(
                    id=info["owner_account_id"],
                    username=owner["username"],
                    role=Role.ADMIN,
                    employee=employee,
                )
                account.set_password(secrets.token_urlsafe(48))
                db.session.add(account)
                db.session.commit()
        finally:
            with child.app_context():
                db.session.remove()
                db.engine.dispose()
        return child

    def get_app(self, slug):
        if not STORE_CODE.fullmatch(slug):
            raise NotFound()
        with self.hub.app_context():
            store = Store.query.filter_by(slug=slug).first()
            if not store:
                raise NotFound()
            info = store_snapshot(store)
        with self.lock:
            if info["database_key"] not in self.apps:
                self.apps[info["database_key"]] = self.build(info)
            return self.apps[info["database_key"]]

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        if path == "/s" or path.startswith("/s/"):
            parts = path.split("/", 3)
            slug = parts[2] if len(parts) > 2 else ""
            try:
                child = self.get_app(slug)
            except NotFound as error:
                return error(environ, start_response)
            child_environ = environ.copy()
            child_environ["shiftspace.hub_prefix"] = environ.get("SCRIPT_NAME", "")
            child_environ["SCRIPT_NAME"] = environ.get("SCRIPT_NAME", "") + "/s/" + slug
            child_environ["PATH_INFO"] = "/" + parts[3] if len(parts) == 4 else "/"
            return child(child_environ, start_response)
        return self.fallback(environ, start_response)
