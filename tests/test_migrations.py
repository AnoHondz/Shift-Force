import secrets
from datetime import date
from pathlib import Path

import pytest
from flask_migrate import upgrade
from sqlalchemy import inspect, text

from app import create_app
from app.extensions import db


def migration_app(tmp_path):
    class Config:
        TESTING = True
        SECRET_KEY = secrets.token_hex(32)
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{(tmp_path / 'migration.db').as_posix()}"
        RATELIMIT_ENABLED = False

    return create_app(Config)


def test_factory_does_not_create_tables(tmp_path):
    app = migration_app(tmp_path)
    with app.app_context():
        assert inspect(db.engine).get_table_names() == []


def test_fresh_migration_and_second_upgrade_are_idempotent(tmp_path):
    app = migration_app(tmp_path)
    with app.app_context():
        upgrade()
        assert "payroll_records" in inspect(db.engine).get_table_names()
        assert db.session.execute(text("PRAGMA foreign_keys")).scalar() == 1
        assert db.session.execute(text("PRAGMA foreign_key_check")).all() == []
        upgrade()
        assert (
            db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()
            == "0002_integrity"
        )


def test_existing_legacy_installation_preserves_records(tmp_path):
    app = migration_app(tmp_path)
    with app.app_context():
        # This is the repository's historical schema, with absent workflow columns/tables.
        raw = db.engine.raw_connection()
        raw.executescript(Path("schema.sql").read_text())
        raw.execute(
            "INSERT INTO employees (id,first_name,last_name,email,job_title,hourly_rate,overtime_multiplier,date_hired) VALUES (1,'Legacy','Employee','legacy@example.com','Engineer',20,1.5,?)",
            (date.today().isoformat(),),
        )
        raw.execute(
            "INSERT INTO payroll_records (employee_id,work_date,hours_worked,regular_hours,overtime_hours,total_pay,notes) VALUES (1,?,45,40,5,950,'Preserve historical weekly entry')",
            (date.today().isoformat(),),
        )
        raw.commit()
        raw.close()
        upgrade()
        record = db.session.execute(
            text(
                "SELECT hours_worked,total_pay,notes,legacy_import,rate_snapshot FROM payroll_records"
            )
        ).one()
        assert record == (45, 950, "Preserve historical weekly entry", 1, 20)
        assert db.session.execute(text("SELECT COUNT(*) FROM employees")).scalar() == 1
        assert db.session.execute(text("PRAGMA foreign_key_check")).all() == []


def test_invalid_legacy_data_stops_migration_without_removing_records(tmp_path):
    app = migration_app(tmp_path)
    with app.app_context():
        upgrade(revision="0001_baseline")
        db.session.execute(
            text(
                "INSERT INTO employees (first_name,last_name,email,job_title,schedule,hourly_rate,overtime_multiplier,date_hired) VALUES ('Invalid','Rate','invalid@example.com','Engineer','Weekdays',-1,1.5,'2026-10-01')"
            )
        )
        db.session.commit()
        with pytest.raises(SystemExit):
            upgrade()
        assert db.session.execute(text("SELECT COUNT(*) FROM employees")).scalar() == 1
        assert db.session.execute(text("SELECT hourly_rate FROM employees")).scalar() == -1
        assert (
            db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()
            == "0001_baseline"
        )


def test_migration_downgrade_refuses_destructive_changes(tmp_path):
    from flask_migrate import downgrade

    app = migration_app(tmp_path)
    with app.app_context():
        upgrade()
        with pytest.raises(SystemExit):
            downgrade(revision="0001_baseline")
        assert (
            db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()
            == "0002_integrity"
        )
