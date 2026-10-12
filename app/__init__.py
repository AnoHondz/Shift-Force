from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, render_template, request
from flask_login import current_user
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from .extensions import csrf, db, limiter, login_manager, migrate
from .models import Notification, Role
from .routes import register_blueprints


def create_app(config_object=None):
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).resolve().parent / "templates"),
        static_folder=str(Path(__file__).resolve().parent / "static"),
    )

    app.config.from_object(config_object or "config.Config")
    if not app.config.get("SECRET_KEY") or len(app.config["SECRET_KEY"]) < 32:
        raise RuntimeError(
            "Set FLASK_SECRET_KEY to a randomly generated value of at least 32 characters."
        )
    if app.config.get("APP_ENV") == "production":
        if (
            app.config.get("DEBUG")
            or not app.config.get("SESSION_COOKIE_SECURE")
            or not app.config.get("REMEMBER_COOKIE_SECURE")
        ):
            raise RuntimeError("Production requires debug disabled and secure cookies.")
        if app.config.get("RATELIMIT_STORAGE_URI", "memory://") == "memory://":
            raise RuntimeError("Production requires a shared RATELIMIT_STORAGE_URI.")
    ZoneInfo(app.config.get("WORKFORCE_TIMEZONE", "UTC"))
    Path(app.instance_path).mkdir(parents=True, exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    migrate.init_app(app, db, render_as_batch=True)
    login_manager.login_view = "auth.login"
    register_blueprints(app)
    from .cli import register_commands

    register_commands(app)

    @login_manager.unauthorized_handler
    def unauthorized():
        if request.path.startswith("/api/") or request.is_json:
            return jsonify(error="Authentication required."), 401
        from flask import redirect, url_for

        return redirect(url_for("auth.login", next=request.full_path))

    @app.errorhandler(IntegrityError)
    @app.errorhandler(StaleDataError)
    def conflict(error):
        db.session.rollback()
        if request.is_json:
            return jsonify(error="This change conflicts with existing records."), 409
        return render_template(
            "error.html",
            message="This change conflicts with existing records. Refresh the page and check your inputs.",
        ), 409

    @app.errorhandler(400)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(409)
    @app.errorhandler(413)
    @app.errorhandler(429)
    def client_error(error):
        if request.path.startswith("/api/") or request.is_json:
            return jsonify(error=error.description), error.code
        return render_template("error.html", message=error.description), error.code

    @app.errorhandler(500)
    def server_error(error):
        db.session.rollback()
        return render_template(
            "error.html", message="Something went wrong. Please contact your administrator."
        ), 500

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; font-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        if current_user.is_authenticated:
            response.headers["Cache-Control"] = "no-store"
        if app.config.get("APP_ENV") == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.template_filter("currency")
    def currency(value):
        amount = 0 if value is None else float(value)
        return f"${amount:,.2f}"

    @app.template_filter("hours")
    def hours(value):
        amount = 0 if value is None else float(value)
        return f"{amount:,.2f}"

    @app.template_filter("localtime")
    def localtime(value):
        from datetime import timezone

        if value is None:
            return "—"
        return (
            value.replace(tzinfo=timezone.utc)
            .astimezone(ZoneInfo(app.config.get("WORKFORCE_TIMEZONE", "UTC")))
            .strftime("%Y-%m-%d %H:%M %Z")
        )

    @app.context_processor
    def notification_context():
        if current_user.is_authenticated:
            unread_count = Notification.query.filter_by(
                user_id=current_user.id, is_read=False
            ).count()
            return {
                "unread_notification_count": unread_count,
                "role_label": Role.label,
                "Role": Role,
            }
        return {"unread_notification_count": 0, "role_label": Role.label, "Role": Role}

    return app
