"""Real separate SQLite connections prove competing commands cannot duplicate work."""

import secrets
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest
from conftest import TEST_PASSWORD

from app import create_app
from app.extensions import db
from app.models import (
    Attendance,
    Employee,
    Notification,
    PayrollRecord,
    Role,
    ScheduledShift,
    ShiftCoverage,
    User,
)
from app.planning import roster_signature
from app.scheduling import monday
from app.time_review import time_preview


@pytest.fixture()
def workforce(tmp_path):
    database = tmp_path / "competing-commands.db"
    app = create_app(
        dict(
            TESTING=True,
            SECRET_KEY=secrets.token_hex(32),
            SQLALCHEMY_DATABASE_URI="sqlite:///" + database.as_posix(),
            RATELIMIT_ENABLED=True,
            RATELIMIT_STORAGE_URI="memory://",
            WTF_CSRF_ENABLED=False,
            WORKFORCE_TIMEZONE="UTC",
        )
    )
    week = monday(datetime.now(timezone.utc).date()) + timedelta(days=7)
    with app.app_context():
        db.create_all()
        for number, role in [
            (1, Role.ADMIN),
            (2, Role.EMPLOYEE),
            (3, Role.EMPLOYEE),
            (4, Role.EMPLOYEE),
        ]:
            employee = Employee(
                id=number,
                first_name="Synthetic",
                last_name=str(number),
                email=f"worker{number}@example.com",
                hourly_rate=20,
                overtime_multiplier=1.5,
            )
            user = User(username=f"worker{number}", employee=employee, role=role)
            user.set_password(TEST_PASSWORD)
            db.session.add(user)
        db.session.commit()
    yield app, database, week
    with app.app_context():
        db.session.remove()
        db.engine.dispose()


def competing_posts(app, usernames, path, values):
    gate = Barrier(2)

    def submit(username):
        with app.test_client() as client:
            assert (
                client.post(
                    "/login", data=dict(username=username, password=TEST_PASSWORD)
                ).status_code
                == 302
            )
            gate.wait(timeout=10)
            return client.post(path, data=values).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        return sorted(pool.map(submit, usernames))


def test_two_coworkers_claim_one_shift_through_separate_connections(workforce):
    app, _, week = workforce
    with app.app_context():
        start = datetime.combine(week, datetime.min.time()) + timedelta(hours=9)
        shift = ScheduledShift(employee_id=2, starts_at=start, ends_at=start + timedelta(hours=8))
        db.session.add(shift)
        db.session.flush()
        offer = ShiftCoverage(shift=shift, offered_by_id=2, shift_version=shift.version_id)
        db.session.add(offer)
        db.session.commit()
        identity = offer.id
    statuses = competing_posts(
        app, ["worker3", "worker4"], f"/schedule/coverage/{identity}/claim", dict(version_id="1")
    )
    assert statuses == [302, 409]
    with app.app_context():
        offer = db.session.get(ShiftCoverage, identity)
        assert offer.status == "Pending" and offer.claimed_by_id in {3, 4}
        assert offer.shift.employee_id == 2
        assert Notification.query.filter_by(title="Coverage needs approval").count() == 1


def test_two_publish_clicks_send_one_batch_notice(workforce):
    app, _, week = workforce
    with app.app_context():
        start = datetime.combine(week, datetime.min.time()) + timedelta(hours=9)
        shift = ScheduledShift(
            employee_id=2, starts_at=start, ends_at=start + timedelta(hours=8), published=False
        )
        db.session.add(shift)
        db.session.commit()
        token = roster_signature([shift])
    statuses = competing_posts(
        app,
        ["worker1", "worker1"],
        "/schedule/publish",
        dict(week=week.isoformat(), draft_token=token),
    )
    assert statuses == [302, 409]
    with app.app_context():
        assert ScheduledShift.query.one().published
        assert Notification.query.filter_by(title="Schedule published").count() == 1


def test_two_preparations_create_one_sourced_timesheet(workforce):
    app, _, next_week = workforce
    week = next_week - timedelta(days=14)
    with app.app_context():
        start = datetime.combine(week, datetime.min.time()) + timedelta(hours=9)
        row = Attendance(employee_id=2, date=week, clock_in_at=start)
        row.complete_shift(start + timedelta(hours=8))
        db.session.add(row)
        db.session.commit()
        token = time_preview(
            db.session.get(Employee, 2), week, timezone.utc, datetime.now(timezone.utc).date()
        )["token"]
    statuses = competing_posts(
        app,
        ["worker1", "worker1"],
        "/payroll/clock-hours",
        dict(week=week.isoformat(), employee_id="2", source_token=token, confirmed="1"),
    )
    assert statuses == [302, 409]
    with app.app_context():
        assert PayrollRecord.query.count() == 1
        assert PayrollRecord.query.one().status == "Pending"
        assert Notification.query.filter_by(title="Clock hours prepared").count() == 1


def test_busy_writer_returns_conflict_without_partial_changes(workforce):
    app, database, week = workforce
    client = app.test_client()
    client.post("/login", data=dict(username="worker1", password=TEST_PASSWORD))
    with app.app_context():
        db.session.connection().exec_driver_sql("PRAGMA busy_timeout=1")
        db.session.commit()
    blocker = sqlite3.connect(database)
    try:
        blocker.execute("BEGIN IMMEDIATE")
        response = client.post(
            "/schedule/publish", data=dict(week=week.isoformat(), draft_token=roster_signature([]))
        )
        assert response.status_code == 409 and b"workplace is busy" in response.data
    finally:
        blocker.rollback()
        blocker.close()
    with app.app_context():
        assert ScheduledShift.query.count() == Notification.query.count() == 0


def test_availability_write_limits_are_per_account_and_leave_reads_available(workforce):
    app, _, _ = workforce
    client = app.test_client()
    client.post("/login", data=dict(username="worker2", password=TEST_PASSWORD))
    values = {f"mode_{day}": "any" for day in range(7)}
    for attempt in range(10):
        assert (
            client.post(
                "/schedule/availability",
                data={**values, "version_id": "0" if attempt == 0 else "1"},
            ).status_code
            == 302
        )
    assert (
        client.post("/schedule/availability", data={**values, "version_id": "1"}).status_code == 429
    )
    assert client.get("/schedule/availability").status_code == 200
    other = app.test_client()
    other.post("/login", data=dict(username="worker3", password=TEST_PASSWORD))
    assert (
        other.post("/schedule/availability", data={**values, "version_id": "0"}).status_code == 302
    )
