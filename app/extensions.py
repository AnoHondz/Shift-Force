import sqlite3

from flask import current_app
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import event
from sqlalchemy.engine import Engine


@event.listens_for(Engine, "connect")
def sqlite_foreign_keys(connection, connection_record):
    if isinstance(connection, sqlite3.Connection):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")


db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()
migrate = Migrate()


class AppLimitRegistry:
    """Register route policies once, with an independent limiter for each app."""

    def limit(self, value, **options):
        def decorate(view):
            view._workforce_limits = (*getattr(view, "_workforce_limits", ()), (value, options))
            return view

        return decorate

    def init_app(self, app):
        instance = Limiter(
            key_func=get_remote_address,
            default_limits=[],
            key_prefix=app.config.get("STORE_SLUG") or "main",
        )
        instance.init_app(app)
        app.extensions["workforce_limiter"] = instance

    def bind(self, app):
        instance = app.extensions["workforce_limiter"]
        for endpoint, view in list(app.view_functions.items()):
            for value, options in getattr(view, "_workforce_limits", ()):
                view = instance.limit(value, **options)(view)
            app.view_functions[endpoint] = view

    @property
    def storage(self):
        return current_app.extensions["workforce_limiter"].storage


limiter = AppLimitRegistry()
login_manager.login_view = "auth.login"
login_manager.login_message_category = "error"
login_manager.login_message = "Please log in to continue."
