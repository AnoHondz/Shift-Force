import re
from datetime import datetime, timedelta

import pytest
from conftest import TEST_PASSWORD
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    Attendance,
    AuditLog,
    Employee,
    Role,
    ScheduledShift,
    TeamMessage,
    User,
    utcnow,
)
from app.routes.workplace import parse_shift_time


def login(client, username="admin_user"):
    return client.post("/login", data={"username": username, "password": TEST_PASSWORD})


def punch_data(users):
    start = (utcnow() - timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)
    return dict(
        employee_id=users["employee_profile_id"],
        clock_in_at=start.isoformat(),
        clock_out_at=(start + timedelta(hours=8)).isoformat(),
        reason="Forgot both punches",
    )


def schedule_data(users):
    start = (utcnow() + timedelta(days=2)).replace(hour=9, minute=0, second=0, microsecond=0)
    return dict(
        employee_id=users["employee_profile_id"],
        starts_at=start.isoformat(),
        ends_at=(start + timedelta(hours=8)).isoformat(),
        notes="Front desk",
    )


@pytest.mark.parametrize("role", Role.CHOICES)
def test_manager_tools_permissions(client, app, users, role):
    with app.app_context():
        db.session.get(User, users["admin_id"]).role = role
        db.session.commit()
    login(client)
    expected = 302 if role in Role.LEAVE_REVIEW else 403
    assert client.post("/manage/punches", data=punch_data(users)).status_code == expected
    assert client.post("/schedule", data=schedule_data(users)).status_code == expected
    assert client.get("/manage").status_code == (200 if role in Role.ADMIN_LIKE else 403)


def test_add_and_correct_missed_punches_preserves_identity_audit_and_stale_check(
    client, app, users
):
    login(client)
    data = punch_data(users)
    assert client.post("/manage/punches", data=data).status_code == 302
    with app.app_context():
        record = Attendance.query.one()
        record_id, version = record.id, record.version_id
        assert record.hours_worked == 8
    data.update(
        record_id=record_id,
        version_id=version,
        employee_id=users["admin_employee_id"],
        clock_out_at=datetime.fromisoformat(data["clock_in_at"]) + timedelta(hours=7),
    )
    data["clock_out_at"] = data["clock_out_at"].isoformat()
    assert client.post("/manage/punches", data=data).status_code == 302
    assert client.post("/manage/punches", data=data).status_code == 409
    with app.app_context():
        record = Attendance.query.one()
        assert record.employee_id == users["employee_profile_id"] and record.hours_worked == 7
        audit = (
            AuditLog.query.filter_by(action_type="attendance_corrected")
            .order_by(AuditLog.id.desc())
            .first()
        )
        assert audit.target_id == str(record.id)
        assert "8.0 hours" in audit.description and "7.0 hours" in audit.description
        assert "Reason: Forgot both punches" in audit.description


@pytest.mark.parametrize("change", ["reason", "future", "backwards", "long", "invalid"])
def test_invalid_punches_rejected_without_records(client, app, users, change):
    login(client)
    data = punch_data(users)
    if change == "reason":
        data["reason"] = ""
    elif change == "future":
        data["clock_out_at"] = (utcnow() + timedelta(hours=1)).isoformat()
    elif change == "backwards":
        data["clock_out_at"] = data["clock_in_at"]
    elif change == "long":
        data["clock_out_at"] = (
            datetime.fromisoformat(data["clock_in_at"]) + timedelta(hours=25)
        ).isoformat()
    else:
        data["clock_in_at"] = "bad"
    assert client.post("/manage/punches", data=data).status_code == 400
    with app.app_context():
        assert Attendance.query.count() == 0


def test_close_missed_clockout_and_block_overlapping_and_archived_punches(client, app, users):
    login(client)
    data = punch_data(users)
    end = data.pop("clock_out_at")
    assert client.post("/manage/punches", data=data).status_code == 302
    assert client.post("/manage/punches", data=data).status_code == 400
    with app.app_context():
        record = Attendance.query.one()
        record_id, version = record.id, record.version_id
    data.update(clock_out_at=end, record_id=record_id, version_id=version)
    assert client.post("/manage/punches", data=data).status_code == 302
    with app.app_context():
        db.session.get(Employee, users["employee_profile_id"]).archived = True
        db.session.commit()
        data["version_id"] = Attendance.query.one().version_id
    assert client.post("/manage/punches", data=data).status_code == 400


def test_schedule_edit_cancel_ownership_and_overnight(client, app, users):
    login(client)
    data = schedule_data(users)
    start = datetime.fromisoformat(data["starts_at"]).replace(hour=22)
    data.update(starts_at=start.isoformat(), ends_at=(start + timedelta(hours=8)).isoformat())
    assert client.post("/schedule", data=data).status_code == 302
    assert client.post("/schedule", data=data).status_code == 400
    with app.app_context():
        shift = ScheduledShift.query.one()
        shift_id, version = shift.id, shift.version_id
    data.update(shift_id=shift_id, version_id=version, notes="Overnight desk")
    assert client.post("/schedule", data=data).status_code == 302
    assert client.post("/schedule", data=data).status_code == 409
    client.post("/logout")
    login(client, "employee_user")
    assert (
        b"Overnight desk" in client.get("/schedule").data
        or b"Overnight desk" in client.get("/schedule?days=7").data
    )
    assert client.get(f"/schedule?edit={shift_id}").status_code == 403
    assert (
        client.post(f"/schedule/{shift_id}/cancel", data={"version_id": version}).status_code == 403
    )
    client.post("/logout")
    login(client)
    with app.app_context():
        version = ScheduledShift.query.one().version_id
    assert (
        client.post(f"/schedule/{shift_id}/cancel", data={"version_id": version}).status_code == 302
    )
    with app.app_context():
        assert ScheduledShift.query.one().cancelled
        assert AuditLog.query.filter_by(action_type="schedule_cancelled").count() == 1
    assert b"Overnight desk" not in client.get("/employee/dashboard").data


def test_employee_schedule_cannot_read_other_employee_shifts(client, app, users):
    login(client)
    data = schedule_data(users)
    data.update(employee_id=users["admin_employee_id"], notes="Private manager shift")
    client.post("/schedule", data=data)
    client.post("/logout")
    login(client, "employee_user")
    assert b"Private manager shift" not in client.get("/schedule?employee_id=1").data
    assert b"Private manager shift" not in client.get("/employee/dashboard").data


@pytest.mark.parametrize("timestamp", ["2026-03-08T02:30", "2026-11-01T01:30"])
def test_clock_change_inputs_rejected(app, timestamp):
    app.config["WORKFORCE_TIMEZONE"] = "America/New_York"
    with app.app_context(), pytest.raises(ValueError, match="clock change"):
        parse_shift_time(timestamp)


def test_normal_local_time_and_explicit_offset_roundtrip(app):
    app.config["WORKFORCE_TIMEZONE"] = "America/New_York"
    with app.app_context():
        assert parse_shift_time("2026-10-12T09:00").isoformat() == "2026-10-12T13:00:00"
        assert parse_shift_time("2026-11-01T01:30-05:00").isoformat() == "2026-11-01T06:30:00"


@pytest.mark.parametrize("table", ["attendance_records", "scheduled_shifts"])
def test_database_blocks_overlapping_shift_writes(app, users, table):
    with app.app_context():
        start = utcnow()
        if table == "attendance_records":
            first = Attendance(employee_id=users["employee_profile_id"], clock_in_at=start)
            first.complete_shift(start + timedelta(hours=8))
            second = Attendance(
                employee_id=users["employee_profile_id"], clock_in_at=start + timedelta(hours=1)
            )
        else:
            first = ScheduledShift(
                employee_id=users["employee_profile_id"],
                starts_at=start,
                ends_at=start + timedelta(hours=8),
            )
            second = ScheduledShift(
                employee_id=users["employee_profile_id"],
                starts_at=start + timedelta(hours=1),
                ends_at=start + timedelta(hours=2),
            )
        db.session.add(first)
        db.session.commit()
        db.session.add(second)
        with pytest.raises(IntegrityError, match="overlaps"):
            db.session.commit()
        db.session.rollback()
        assert db.session.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() == 1


def test_shared_chat_forces_sender_escapes_html_and_polls_between_accounts(client, app, users):
    assert client.get("/api/chat/messages").status_code == 401
    login(client, "employee_user")
    content = '<script>alert("hello")</script>\nMeet at the front desk'
    response = client.post(
        "/api/chat/messages", json={"body": content, "user_id": users["admin_id"]}
    )
    assert response.status_code == 201
    message_id = response.json["message"]["id"]
    assert response.json["message"]["author"] == "Regular Employee"
    assert b"&lt;script&gt;" in client.get("/chat").data
    assert content.encode() not in client.get("/chat").data
    client.post("/logout")
    login(client)
    assert client.get("/api/chat/messages").json["messages"][0]["body"] == content
    assert client.get(f"/api/chat/messages?after={message_id}").json["messages"] == []
    response = client.post("/api/chat/messages", json={"body": "See you there"})
    assert (
        client.get(f"/api/chat/messages?after={message_id}").json["messages"][0]["body"]
        == "See you there"
    )
    with app.app_context():
        assert db.session.get(TeamMessage, message_id).user_id == users["employee_id"]


@pytest.mark.parametrize("payload", [{}, {"body": " "}, {"body": 12}, {"body": "x" * 2001}, []])
def test_chat_validates_message_body(client, users, payload):
    login(client)
    assert client.post("/api/chat/messages", json=payload).status_code == 400
    assert client.get("/api/chat/messages?after=-1").status_code == 400


def test_csrf_required_for_chat_and_manager_changes(client, app, users):
    login(client)
    app.config["WTF_CSRF_ENABLED"] = True
    assert client.post("/api/chat/messages", json={"body": "Hi"}).status_code == 400
    assert client.post("/schedule", data=schedule_data(users)).status_code == 400
    assert client.post("/manage/punches", data=punch_data(users)).status_code == 400
    token = (
        re.search(rb'name="csrf-token" content="([^"]+)"', client.get("/dashboard").data)
        .group(1)
        .decode()
    )
    assert (
        client.post(
            "/api/chat/messages", json={"body": "Hi"}, headers={"X-CSRFToken": token}
        ).status_code
        == 201
    )


def test_profile_required_for_chat_and_simple_home_navigation(client, app, users):
    login(client)
    page = client.get("/dashboard").data
    assert b"Manage workplace" in page and b"Team chat" in page and b"Coming up" in page
    assert b"Total Headcount" not in page and b"Payroll This Week" not in page
    with app.app_context():
        db.session.get(User, users["admin_id"]).employee_id = None
        db.session.commit()
    assert client.get("/api/chat/messages").status_code == 403
    assert client.post("/api/chat/messages", json={"body": "Hi"}).status_code == 403
