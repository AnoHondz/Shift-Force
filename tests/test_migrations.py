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
            == "0004_channels"
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


def test_workplace_upgrade_preserves_existing_punches_and_initializes_empty_tools(tmp_path):
    app = migration_app(tmp_path)
    with app.app_context():
        upgrade(revision="0002_integrity")
        db.session.execute(
            text(
                "INSERT INTO employees (id,first_name,last_name,email,job_title,schedule,hourly_rate,overtime_multiplier,date_hired,archived) VALUES (1,'Existing','Employee','existing@example.com','Engineer','Weekdays',30,1.5,'2026-10-01',0)"
            )
        )
        db.session.execute(
            text(
                "INSERT INTO attendance_records (employee_id,clock_in_at,clock_out_at,hours_worked,status,date,created_at) VALUES (1,'2026-10-09 13:00:00','2026-10-09 21:00:00',8,'Completed','2026-10-09','2026-10-09 13:00:00')"
            )
        )
        before = db.session.execute(text("SELECT * FROM attendance_records")).one()
        db.session.commit()
        upgrade()
        after = db.session.execute(text("SELECT * FROM attendance_records")).one()
        assert tuple(after[:-1]) == tuple(before) and after[-1] == 1
        assert db.session.execute(text("SELECT COUNT(*) FROM scheduled_shifts")).scalar() == 0
        assert db.session.execute(text("SELECT COUNT(*) FROM team_messages")).scalar() == 0
        triggers = (
            db.session.execute(text("SELECT name FROM sqlite_master WHERE type='trigger'"))
            .scalars()
            .all()
        )
        assert "attendance_records_no_overlap_update" in triggers
        assert "scheduled_shifts_no_overlap_insert" in triggers


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


def test_channels_upgrade_keeps_existing_conversation_in_general(tmp_path):
    app = migration_app(tmp_path)
    with app.app_context():
        upgrade(revision="0003_workplace")
        db.session.execute(
            text(
                "INSERT INTO employees (id,first_name,last_name,email,job_title,schedule,hourly_rate,overtime_multiplier,date_hired,archived) VALUES (1,'Existing','Coworker','chat@example.com','Engineer','Weekdays',30,1.5,'2026-10-01',0)"
            )
        )
        db.session.execute(
            text(
                "INSERT INTO users (id,username,password_hash,role,employee_id) VALUES (1,'coworker',:hash,'employee',1)"
            ),
            {"hash": secrets.token_hex(32)},
        )
        db.session.execute(
            text(
                "INSERT INTO team_messages (id,user_id,body,created_at) VALUES (1,1,'Keep this conversation','2026-10-12 13:00:00')"
            )
        )
        before = tuple(
            db.session.execute(text("SELECT id,user_id,body,created_at FROM team_messages")).one()
        )
        db.session.commit()
        upgrade()
        after = tuple(
            db.session.execute(text("SELECT id,user_id,body,created_at FROM team_messages")).one()
        )
        assert after == before
        assert db.session.execute(text("SELECT channel_id FROM team_messages")).scalar() == 1
        assert (
            db.session.execute(text("SELECT name FROM team_channels WHERE id=1")).scalar()
            == "general"
        )
        assert db.session.execute(text("SELECT COUNT(*) FROM chat_attachments")).scalar() == 0
        assert db.session.execute(text("PRAGMA foreign_key_check")).all() == []


def test_migration_downgrade_refuses_destructive_changes(tmp_path):
    from flask_migrate import downgrade

    app = migration_app(tmp_path)
    with app.app_context():
        upgrade()
        with pytest.raises(SystemExit):
            downgrade(revision="0001_baseline")
        assert (
            db.session.execute(text("SELECT version_num FROM alembic_version")).scalar()
            == "0004_channels"
        )
