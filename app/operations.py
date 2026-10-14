"""Health, readiness, maintenance and privacy-safe request telemetry."""

import json
import logging
import math
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy import text

from .extensions import db
from .models import Store

ops_bp = Blueprint("ops", __name__)
SCHEMA_HEAD = "0006_operations"


def data_root(app=None):
    app = app or current_app
    return Path(app.config.get("DATA_ROOT") or app.instance_path).resolve()


@ops_bp.get("/healthz")
def health():
    response = jsonify(status="ok")
    response.headers["Cache-Control"] = "no-store"
    return response


@ops_bp.get("/readyz")
def ready():
    checks = {"database": False, "rate_limits": False, "backups": False}
    try:
        checks["database"] = (
            db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()
            == SCHEMA_HEAD
        )
        if not current_app.config.get("STORE_SLUG"):
            router = current_app.extensions["stores"]
            for store in Store.query.all():
                path = router.database_path({"database_key": store.database_key})
                with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
                    checks["database"] = (
                        checks["database"]
                        and connection.execute(
                            "SELECT version_num FROM alembic_version"
                        ).fetchone()[0]
                        == SCHEMA_HEAD
                    )
    except Exception:
        checks["database"] = False
        current_app.logger.warning(
            "Readiness dependency check failed", extra={"event": "readiness_failed"}
        )
        db.session.rollback()
    production = current_app.config.get("APP_ENV") == "production"
    try:
        enabled = current_app.config.get("RATELIMIT_ENABLED", True)
        checks["rate_limits"] = (
            bool(current_app.extensions["workforce_limiter"].storage.check())
            if enabled
            else not production
        )
    except Exception:
        current_app.logger.warning("Readiness rate limit storage check failed")
    if not production:
        checks["backups"] = True
    else:
        try:
            stamp = json.loads((data_root() / "backups" / "status.json").read_text())
            now = time.time()
            interval = current_app.config.get("BACKUP_INTERVAL_SECONDS", 86400)
            checks["backups"] = all(
                type(stamp[field]) in {int, float}
                and math.isfinite(stamp[field])
                and -60 <= now - stamp[field] <= 2 * interval
                for field in ("offsite_verified_at", "restore_verified_at")
            )
        except (OSError, ValueError, KeyError, TypeError):
            current_app.logger.warning("Readiness backup status check failed")
    healthy = all(checks.values()) and not (data_root() / "maintenance.lock").exists()
    response = jsonify(status="ready" if healthy else "unavailable", checks=checks)
    response.headers["Cache-Control"] = "no-store"
    return response, 200 if healthy else 503


def configure_operations(app):
    if app.config.get("APP_ENV") == "production":
        from urllib.parse import urlsplit

        from cryptography.fernet import Fernet

        if not app.config.get("MFA_ENCRYPTION_KEY"):
            raise RuntimeError("Production requires a persistent MFA_ENCRYPTION_KEY.")
        Fernet(app.config["MFA_ENCRYPTION_KEY"])
        if not app.config.get("REQUIRE_PRIVILEGED_MFA", True):
            raise RuntimeError("Production management requires two-step verification.")
        if not app.config.get("REQUIRE_PAYROLL_REVIEW", True):
            raise RuntimeError("Production payroll requires operator review and reconciliation.")
        if not app.config.get("RATELIMIT_ENABLED", True):
            raise RuntimeError("Production requires enabled, shared rate limits.")
        origin = urlsplit(app.config.get("PUBLIC_ORIGIN", ""))
        if (
            origin.scheme != "https"
            or not origin.hostname
            or origin.path not in {"", "/"}
            or origin.username
            or origin.password
            or origin.query
            or origin.fragment
        ):
            raise RuntimeError("Production requires a canonical HTTPS PUBLIC_ORIGIN.")
        root = data_root(app)
        database = Path(db.engine.url.database).resolve() if db.engine.url.database else None
        stores = Path(app.config.get("STORE_DATABASE_ROOT") or root / "stores").resolve()
        if not database or not database.is_relative_to(root) or not stores.is_relative_to(root):
            raise RuntimeError("Hub and store databases must stay on the durable DATA_ROOT volume.")
        app.config["TRUSTED_HOSTS"] = [origin.hostname]
        app.config["PREFERRED_URL_SCHEME"] = "https"
        app.logger.setLevel(logging.INFO)
        if app.config.get("TRUST_PROXY"):
            from werkzeug.middleware.proxy_fix import ProxyFix

            app.wsgi_app = ProxyFix(
                app.wsgi_app, x_for=1, x_proto=1, x_host=0, x_port=0, x_prefix=0
            )

    app.register_blueprint(ops_bp)

    @app.before_request
    def begin_request():
        g.request_started = time.monotonic()
        g.request_id = secrets.token_hex(8)
        if (
            request.endpoint not in {"ops.health", "ops.ready", "static"}
            and (data_root(app) / "maintenance.lock").exists()
        ):
            response = jsonify(
                error="Workplace maintenance is in progress. Please try again shortly."
            )
            response.headers["Retry-After"] = "30"
            return response, 503

    @app.after_request
    def request_metrics(response):
        request_id = getattr(g, "request_id", secrets.token_hex(8))
        response.headers["X-Request-ID"] = request_id
        if app.config.get("APP_ENV") == "production":
            # No query strings, bodies, reset links, codes, usernames, IPs or employee data.
            payload = dict(
                event="http_request",
                request_id=request_id,
                endpoint=request.endpoint,
                method=request.method,
                status=response.status_code,
                duration_ms=round(
                    (time.monotonic() - getattr(g, "request_started", time.monotonic())) * 1000, 2
                ),
                store=app.config.get("STORE_SLUG") or "hub",
            )
            app.logger.log(
                logging.ERROR if response.status_code >= 500 else logging.INFO, json.dumps(payload)
            )
        return response
