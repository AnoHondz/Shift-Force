import re
from datetime import date, datetime, timedelta

import pytest
from conftest import TEST_PASSWORD

from app.extensions import db
from app.models import AuditLog, LeaveRequest, PayrollRecord, ScheduledShift, User

WEEK = date(2026, 10, 19)


def sign_in(client, username="admin_user"):
    return client.post("/login", data={"username": username, "password": TEST_PASSWORD})


def shift_data(users, **changes):
    values = dict(
        employee_id=users["employee_profile_id"],
        week=WEEK.isoformat(),
        starts_at="2026-10-19T09:00",
        ends_at="2026-10-19T17:00",
        notes="Front counter",
    )
    values.update(changes)
    return values


def totals(response):
    return re.search(rb'id="schedule-totals">(.*?)</p>', response.data).group(1)


def simple_data(users, **changes):
    values = dict(
        employee_id=users["employee_profile_id"],
        week=WEEK.isoformat(),
        simple_schedule="1",
        shift_days=["0"],
        start_time="09:00",
        end_time="17:00",
        notes="",
    )
    values.update(changes)
    return values


def test_simple_days_save_exact_selection_without_javascript(client, app, users):
    sign_in(client)
    response = client.post("/schedule", data=simple_data(users, shift_days=["2", "4"]))
    assert response.status_code == 302
    with app.app_context():
        shifts = ScheduledShift.query.order_by(ScheduledShift.starts_at).all()
        assert [shift.starts_at.day for shift in shifts] == [21, 23]
        assert all(shift.ends_at - shift.starts_at == timedelta(hours=8) for shift in shifts)
        assert AuditLog.query.filter_by(action_type="schedule_created").count() == 2


@pytest.mark.parametrize(
    "changes",
    [
        dict(shift_days=[]),
        dict(shift_days=["7"]),
        dict(shift_days=["0"] * 8),
        dict(shift_days=["00"]),
        dict(start_time="25:00"),
        dict(end_time="17:00:00"),
        dict(end_time="09:00"),
        dict(repeat_days=["2"]),
    ],
)
def test_simple_invalid_selection_never_writes(client, app, users, changes):
    sign_in(client)
    assert client.post("/schedule", data=simple_data(users, **changes)).status_code == 400
    with app.app_context():
        assert ScheduledShift.query.count() == 0
        assert AuditLog.query.filter_by(action_type="schedule_created").count() == 0


def test_simple_conflict_keeps_all_selected_days_and_times(client, app, users):
    sign_in(client)
    client.post("/schedule", data=simple_data(users, shift_days=["4"]))
    response = client.post(
        "/schedule",
        data=simple_data(users, shift_days=["0", "4"], start_time="08:00", notes="Keep this draft"),
    )
    assert response.status_code == 400
    assert b'data-date="2026-10-19" checked' in response.data
    assert b'data-date="2026-10-23" checked' in response.data
    assert b'name="start_time" value="08:00"' in response.data
    assert b'value="Keep this draft"' in response.data
    with app.app_context():
        assert ScheduledShift.query.count() == 1


def test_simple_overnight_observes_store_zone_and_next_day_leave(client, app, users):
    app.config["WORKFORCE_TIMEZONE"] = "America/New_York"
    sign_in(client)
    with app.app_context():
        add_leave(users, WEEK + timedelta(days=1))
    values = simple_data(users, start_time="22:00", end_time="06:00")
    assert client.post("/schedule", data=values).status_code == 400
    values["shift_days"] = ["2"]
    assert client.post("/schedule", data=values).status_code == 302
    with app.app_context():
        shift = ScheduledShift.query.one()
        assert shift.starts_at == datetime(2026, 10, 22, 2)
        assert shift.ends_at == datetime(2026, 10, 22, 10)


def test_simple_clock_change_rejects_entire_group(client, app, users):
    app.config["WORKFORCE_TIMEZONE"] = "America/New_York"
    sign_in(client)
    values = simple_data(
        users, week="2026-03-02", shift_days=["0", "6"], start_time="02:30", end_time="04:30"
    )
    assert client.post("/schedule", data=values).status_code == 400
    with app.app_context():
        assert ScheduledShift.query.count() == 0


def test_simple_edit_retains_identity_and_rejects_multiple_days(client, app, users):
    sign_in(client)
    client.post("/schedule", data=simple_data(users))
    with app.app_context():
        shift = ScheduledShift.query.one()
        shift_id, version = shift.id, shift.version_id
    values = simple_data(
        users,
        shift_id=shift_id,
        version_id=version,
        shift_days=[],
        shift_date="2026-10-21",
        end_time="18:00",
    )
    page = client.get(f"/schedule?week={WEEK}&edit={shift_id}")
    assert b'name="shift_date"' in page.data and b'name="shift_days"' not in page.data
    values["shift_days"] = ["0", "2"]
    assert client.post("/schedule", data=values).status_code == 400
    values["shift_days"] = []
    assert client.post("/schedule", data=values).status_code == 302
    assert client.post("/schedule", data=values).status_code == 409
    with app.app_context():
        assert ScheduledShift.query.count() == 1
        shift = db.session.get(ScheduledShift, shift_id)
        assert shift.starts_at.day == 21 and shift.ends_at.hour == 18


@pytest.mark.parametrize(
    "zone, day, end",
    [
        ("UTC", "2026-10-19", "2026-10-20T09:00"),
        ("America/New_York", "2026-03-07", "2026-03-08T10:00"),
    ],
)
def test_simple_edit_preserves_existing_full_day_shift(client, app, users, zone, day, end):
    app.config["WORKFORCE_TIMEZONE"] = zone
    sign_in(client)
    assert (
        client.post(
            "/schedule", data=shift_data(users, starts_at=day + "T09:00", ends_at=end)
        ).status_code
        == 302
    )
    with app.app_context():
        shift = ScheduledShift.query.one()
        shift_id, version, original_end = shift.id, shift.version_id, shift.ends_at
    values = simple_data(
        users,
        shift_id=shift_id,
        version_id=version,
        shift_date=day,
        shift_days=[],
        start_time="09:00",
        end_time=end[11:16],
        notes="Updated note",
    )
    assert client.post("/schedule", data=values).status_code == 302
    with app.app_context():
        shift = db.session.get(ScheduledShift, shift_id)
        assert shift.ends_at == original_end and shift.notes == "Updated note"


def add_leave(users, day=WEEK, status="Approved"):
    db.session.add(
        LeaveRequest(
            employee_id=users["employee_profile_id"],
            request_type="Vacation",
            start_date=day,
            end_date=day,
            status=status,
            reason="Do not expose this reason",
        )
    )
    db.session.commit()


def test_repeat_saves_one_group_with_audits_and_no_payroll(client, app, users):
    sign_in(client)
    response = client.post("/schedule", data=shift_data(users, repeat_days=["0", "2", "4"]))
    assert response.status_code == 302 and response.location.endswith("week=2026-10-19")
    with app.app_context():
        shifts = ScheduledShift.query.order_by(ScheduledShift.starts_at).all()
        assert [shift.starts_at.day for shift in shifts] == [19, 21, 23]
        assert all(shift.notes == "Front counter" for shift in shifts)
        assert AuditLog.query.filter_by(action_type="schedule_created").count() == 3
        assert PayrollRecord.query.count() == 0
    assert b"24.00" in totals(client.get(response.location))


def test_repeat_conflict_is_atomic_and_keeps_form_values(client, app, users):
    sign_in(client)
    conflict = shift_data(users, starts_at="2026-10-23T08:00", ends_at="2026-10-23T10:00")
    client.post("/schedule", data=conflict)
    response = client.post(
        "/schedule", data=shift_data(users, repeat_days=["2", "4"], notes="Keep my draft")
    )
    assert response.status_code == 400 and b"already has a shift" in response.data
    assert (
        b'value="Keep my draft"' in response.data
        and b'data-date="2026-10-21" checked' in response.data
    )
    assert b'name="start_time" value="09:00"' in response.data
    with app.app_context():
        assert ScheduledShift.query.count() == 1
        assert AuditLog.query.filter_by(action_type="schedule_created").count() == 1


@pytest.mark.parametrize("values", [["7"], ["-1"], ["Wed"], [""], ["0"] * 8])
def test_repeat_rejects_invalid_days_without_records(client, app, users, values):
    sign_in(client)
    assert client.post("/schedule", data=shift_data(users, repeat_days=values)).status_code == 400
    with app.app_context():
        assert ScheduledShift.query.count() == 0


@pytest.mark.parametrize("status", ["Approved", "Pending", "Rejected"])
def test_only_approved_leave_blocks_scheduling(client, app, users, status):
    with app.app_context():
        add_leave(users, status=status)
    sign_in(client)
    response = client.post("/schedule", data=shift_data(users))
    assert response.status_code == (400 if status == "Approved" else 302)
    with app.app_context():
        assert ScheduledShift.query.count() == (0 if status == "Approved" else 1)
    page = client.get("/schedule?week=2026-10-19")
    assert (b"Approved time off" in page.data) == (status == "Approved")
    assert b"Do not expose this reason" not in page.data


def test_overnight_leave_boundary_is_exclusive_and_batch_safe(client, app, users):
    with app.app_context():
        add_leave(users, WEEK + timedelta(days=1))
    sign_in(client)
    response = client.post(
        "/schedule",
        data=shift_data(users, starts_at="2026-10-19T22:00", ends_at="2026-10-20T06:00"),
    )
    assert response.status_code == 400
    assert (
        client.post(
            "/schedule",
            data=shift_data(users, starts_at="2026-10-19T22:00", ends_at="2026-10-20T00:00"),
        ).status_code
        == 302
    )
    assert (
        client.post(
            "/schedule",
            data=shift_data(
                users, starts_at="2026-10-21T09:00", ends_at="2026-10-21T17:00", repeat_days=["1"]
            ),
        ).status_code
        == 400
    )
    with app.app_context():
        assert ScheduledShift.query.count() == 1


def test_week_totals_split_overnight_at_week_boundary(client, app, users):
    sign_in(client)
    response = client.post(
        "/schedule",
        data=shift_data(users, starts_at="2026-10-25T22:00", ends_at="2026-10-26T06:00"),
    )
    assert response.status_code == 302
    first = client.get("/schedule?week=2026-10-19")
    second = client.get("/schedule?week=2026-10-26")
    assert b"2.00" in totals(first) and b"6.00" in totals(second)
    assert b"Ends Mon" in first.data and b"Started previous day" in second.data
    assert b"Continues until 6:00 AM" in second.data


def test_repeat_preserves_local_hours_across_daylight_saving(client, app, users):
    app.config["WORKFORCE_TIMEZONE"] = "America/New_York"
    sign_in(client)
    response = client.post(
        "/schedule",
        data=shift_data(
            users, starts_at="2026-03-02T01:30", ends_at="2026-03-02T03:30", repeat_days=["6"]
        ),
    )
    assert response.status_code == 302
    with app.app_context():
        shifts = ScheduledShift.query.order_by(ScheduledShift.starts_at).all()
        assert shifts[0].ends_at - shifts[0].starts_at == timedelta(hours=2)
        assert shifts[1].starts_at == datetime(2026, 3, 8, 6, 30)
        assert shifts[1].ends_at - shifts[1].starts_at == timedelta(hours=1)


@pytest.mark.parametrize(
    "start,end",
    [("2026-03-02T02:30", "2026-03-02T04:30"), ("2026-10-26T01:30", "2026-10-26T03:30")],
)
def test_repeat_rejects_clock_change_day_without_partial_save(client, app, users, start, end):
    app.config["WORKFORCE_TIMEZONE"] = "America/New_York"
    sign_in(client)
    response = client.post(
        "/schedule", data=shift_data(users, starts_at=start, ends_at=end, repeat_days=["6"])
    )
    assert response.status_code == 400 and b"clock change" in response.data
    with app.app_context():
        assert ScheduledShift.query.count() == 0


def test_edit_cannot_repeat_and_stale_form_cannot_overwrite(client, app, users):
    sign_in(client)
    client.post("/schedule", data=shift_data(users))
    with app.app_context():
        shift = ScheduledShift.query.one()
        shift_id, version = shift.id, shift.version_id
    values = shift_data(users, shift_id=shift_id, version_id=version, repeat_days=["2"])
    assert client.post("/schedule", data=values).status_code == 400
    values.pop("repeat_days")
    values["notes"] = "Updated shift"
    assert client.post("/schedule", data=values).status_code == 302
    values["notes"] = "Stale draft"
    response = client.post("/schedule", data=values)
    assert response.status_code == 409 and b"Stale draft" in response.data
    assert f'name="version_id" value="{version}"'.encode() in response.data
    with app.app_context():
        assert ScheduledShift.query.one().notes == "Updated shift"


def test_cancel_retains_selected_week_filter_and_history(client, app, users):
    sign_in(client)
    client.post("/schedule", data=shift_data(users))
    with app.app_context():
        shift = ScheduledShift.query.one()
        shift_id, version = shift.id, shift.version_id
    response = client.post(
        f"/schedule/{shift_id}/cancel",
        data=dict(week=WEEK.isoformat(), staff=users["employee_profile_id"], version_id=version),
    )
    assert response.status_code == 302 and "week=2026-10-19" in response.location
    assert "staff=" in response.location
    assert b"0.00" in totals(client.get(response.location))
    with app.app_context():
        assert ScheduledShift.query.one().cancelled


def test_staff_filter_and_unlinked_employee_never_leak_other_shifts(client, app, users):
    sign_in(client)
    client.post("/schedule", data=shift_data(users, notes="Employee coverage"))
    client.post(
        "/schedule",
        data=shift_data(users, employee_id=users["admin_employee_id"], notes="Manager coverage"),
    )
    page = client.get(f"/schedule?week={WEEK}&staff={users['employee_profile_id']}")
    assert b"Employee coverage" in page.data and b"Manager coverage" not in page.data
    assert b"8.00" in totals(page)
    client.post("/logout")
    sign_in(client, "employee_user")
    page = client.get(f"/schedule?week={WEEK}&staff={users['admin_employee_id']}")
    assert b"Employee coverage" in page.data and b"Manager coverage" not in page.data
    with app.app_context():
        db.session.get(User, users["employee_id"]).employee_id = None
        db.session.commit()
    assert b"Employee coverage" not in client.get(f"/schedule?week={WEEK}").data


def test_later_approved_leave_flags_existing_shift_without_deleting_it(client, app, users):
    sign_in(client)
    client.post("/schedule", data=shift_data(users))
    with app.app_context():
        add_leave(users)
    page = client.get(f"/schedule?week={WEEK}")
    assert b"Overlaps approved time off. Review this shift." in page.data
    with app.app_context():
        assert ScheduledShift.query.count() == 1


@pytest.mark.parametrize(
    "change",
    [
        dict(week="bad"),
        dict(week="1900-01-01"),
        dict(employee_id="9" * 50),
        dict(starts_at="9999-01-01T09:00", ends_at="9999-01-01T17:00"),
        dict(notes="x" * 256),
    ],
)
def test_invalid_inputs_do_not_create_shifts(client, app, users, change):
    sign_in(client)
    assert client.post("/schedule", data=shift_data(users, **change)).status_code == 400
    with app.app_context():
        assert ScheduledShift.query.count() == 0


def test_week_view_does_not_silently_drop_shifts_after_five_hundred(client, app, users):
    with app.app_context():
        start = datetime(2026, 10, 19, 1)
        db.session.add_all(
            ScheduledShift(
                employee_id=users["employee_profile_id"],
                starts_at=start + timedelta(minutes=2 * i),
                ends_at=start + timedelta(minutes=2 * i + 1),
                notes=f"Coverage {i}",
            )
            for i in range(501)
        )
        db.session.commit()
    sign_in(client)
    page = client.get(f"/schedule?week={WEEK}")
    assert b"<strong>501</strong>" in totals(page)
    assert b"Coverage 500" in page.data


def test_database_conflict_rolls_back_entire_repeated_group(client, app, users, monkeypatch):
    sign_in(client)
    client.post(
        "/schedule",
        data=shift_data(users, starts_at="2026-10-23T08:00", ends_at="2026-10-23T10:00"),
    )
    monkeypatch.setattr("app.routes.workplace.validate_availability", lambda *args, **kwargs: None)
    response = client.post("/schedule", data=shift_data(users, repeat_days=["4"]))
    assert response.status_code == 409
    with app.app_context():
        assert ScheduledShift.query.count() == 1
        assert AuditLog.query.filter_by(action_type="schedule_created").count() == 1


def test_adjacent_shifts_are_allowed_and_day_link_prefills_date(client, app, users):
    sign_in(client)
    client.post("/schedule", data=shift_data(users))
    assert (
        client.post(
            "/schedule",
            data=shift_data(users, starts_at="2026-10-19T17:00", ends_at="2026-10-19T21:00"),
        ).status_code
        == 302
    )
    page = client.get("/schedule?week=2026-10-19&on=2026-10-23")
    assert b'data-date="2026-10-23" checked' in page.data
    assert client.get("/schedule?week=2026-10-19&on=2026-10-26").status_code == 400


def test_invalid_cancel_filter_never_cancels_shift(client, app, users):
    sign_in(client)
    client.post("/schedule", data=shift_data(users))
    with app.app_context():
        shift = ScheduledShift.query.one()
        shift_id, version = shift.id, shift.version_id
    response = client.post(
        f"/schedule/{shift_id}/cancel", data=dict(version_id=version, staff="bad")
    )
    assert response.status_code == 400
    with app.app_context():
        assert not ScheduledShift.query.one().cancelled


def test_invalid_employee_preserves_edit_identity_in_the_form(client, app, users):
    sign_in(client)
    client.post("/schedule", data=shift_data(users))
    with app.app_context():
        shift = ScheduledShift.query.one()
        shift_id, version = shift.id, shift.version_id
    response = client.post(
        "/schedule",
        data=shift_data(
            users, shift_id=shift_id, version_id=version, employee_id="", notes="Keep this edit"
        ),
    )
    assert response.status_code == 400
    assert f'name="shift_id" value="{shift_id}"'.encode() in response.data
    assert b"Keep this edit" in response.data
    with app.app_context():
        assert (
            ScheduledShift.query.count() == 1
            and ScheduledShift.query.one().notes == "Front counter"
        )
