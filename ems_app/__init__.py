import os
from pathlib import Path

from flask import Flask

from . import auth, db, employees, reports


def create_app():
    base_dir = Path(__file__).resolve().parent.parent
    app = Flask(
        __name__,
        template_folder=str(base_dir / "templates"),
    )
    app.config.update(
        SECRET_KEY=os.getenv("FLASK_SECRET_KEY", "ano-hondz-workforce-secret"),
        DATABASE=str(base_dir / "employee_mgmt.db"),
        DEFAULT_EXEC_USERNAME=os.getenv("EXEC_USERNAME", "executive"),
        DEFAULT_EXEC_PASSWORD=os.getenv("EXEC_PASSWORD", "AnoHondz123"),
        EXECUTIVE_SIGNUP_CODE=os.getenv("EXECUTIVE_SIGNUP_CODE", "AHWL-EXEC-2026"),
    )

    # Register database hooks and blueprints once when the app starts.
    db.init_app(app)
    app.register_blueprint(auth.bp)
    app.register_blueprint(employees.bp)
    app.register_blueprint(reports.bp)

    @app.template_filter("currency")
    def currency(value):
        amount = 0 if value is None else float(value)
        return f"${amount:,.2f}"

    @app.template_filter("hours")
    def hours(value):
        amount = 0 if value is None else float(value)
        return f"{amount:,.2f}"

    return app
