import os
from pathlib import Path

from flask import Flask
from flask_login import current_user
from sqlalchemy import inspect, text

from .extensions import db, login_manager
from .models import Notification, initialize_default_data
from .routes import register_blueprints


def create_app(config_object=None):
    base_dir = Path(__file__).resolve().parent.parent
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).resolve().parent / "templates"),
        static_folder=str(Path(__file__).resolve().parent / "static"),
    )

    app.config.from_object(config_object or "config.Config")

    db.init_app(app)
    login_manager.init_app(app)
    register_blueprints(app)

    @app.template_filter("currency")
    def currency(value):
        amount = 0 if value is None else float(value)
        return f"${amount:,.2f}"

    @app.template_filter("hours")
    def hours(value):
        amount = 0 if value is None else float(value)
        return f"{amount:,.2f}"

    @app.context_processor
    def notification_context():
        if current_user.is_authenticated:
            unread_count = Notification.query.filter_by(user_id=current_user.id, is_read=False).count()
            return {"unread_notification_count": unread_count}
        return {"unread_notification_count": 0}

    with app.app_context():
        db.create_all()
        inspector = inspect(db.engine)
        employee_columns = [column["name"] for column in inspector.get_columns("employees")]
        if "schedule" not in employee_columns:
            db.session.execute(
                text("ALTER TABLE employees ADD COLUMN schedule TEXT NOT NULL DEFAULT 'Mon-Fri 9:00 AM - 5:00 PM'")
            )
            db.session.commit()
        initialize_default_data(app)

    return app
