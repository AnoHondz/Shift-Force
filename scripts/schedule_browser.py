"""Exercise the weekly planner through real forms and dialogs in Chromium."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from playwright.sync_api import expect

from app.extensions import db
from app.models import LeaveRequest, ScheduledShift, User


def verify_schedule_builder(page, browser, origin, app, credentials, completed, capture, errors):
    def rejected_submit():
        previous_errors = len(errors)
        with page.expect_response(
            lambda response: (
                response.url == origin + "/schedule" and response.request.method == "POST"
            )
        ) as rejected:
            page.get_by_role("button", name="Save shift", exact=True).click()
        assert rejected.value.status == 400
        assert errors[previous_errors:] == ["HTTP 400: " + origin + "/schedule"]
        del errors[previous_errors:]

    page.set_viewport_size({"width": 1440, "height": 1000})
    today = datetime.now(ZoneInfo("America/New_York")).date()
    week = today - timedelta(days=today.weekday()) + timedelta(days=28)
    thursday = week + timedelta(days=3)
    with app.app_context():
        employee_id = User.query.filter_by(username="demo.manager").one().employee_id
        admin_id = User.query.filter_by(username="demo.admin").one().id
        db.session.add(
            LeaveRequest(
                employee_id=employee_id,
                request_type="Vacation",
                start_date=thursday,
                end_date=thursday,
                status="Approved",
                reviewed_by_id=admin_id,
            )
        )
        db.session.commit()
    page.goto(origin + "/schedule?week=" + week.isoformat())
    page.locator(f'[data-schedule-day="{week.isoformat()}"]').click()
    expect(page.get_by_label("Start time", exact=True)).to_have_value("09:00")
    page.get_by_label("Employee", exact=True).select_option(str(employee_id))
    page.get_by_text("Add a note", exact=True).click()
    page.get_by_label("Notes (optional)").fill("Opening crew")
    page.get_by_label("Wed", exact=True).check()
    page.get_by_label("Fri", exact=True).check()
    expect(page.locator("#shift-preview")).to_have_text("8 scheduled hours")
    capture(page, "schedule-builder")
    page.get_by_role("button", name="Save 3 shifts", exact=True).click()
    expect(page.get_by_text("3 shifts saved.", exact=True)).to_be_visible()
    expect(page.locator(".scheduled-card")).to_have_count(3)
    expect(page.locator("#schedule-totals")).to_contain_text("24.00")
    page.get_by_label("Show", exact=True).select_option(str(employee_id))
    page.get_by_role("button", name="Apply", exact=True).click()
    with app.app_context():
        shifts = (
            ScheduledShift.query.filter_by(employee_id=employee_id)
            .order_by(ScheduledShift.starts_at)
            .all()
        )
        monday_id, wednesday_id, friday_id = [shift.id for shift in shifts]
    page.locator(f'[data-shift-id="{wednesday_id}"]').get_by_role(
        "link", name="Edit", exact=True
    ).click()
    page.get_by_label("End time", exact=True).fill("18:00")
    expect(page.locator("#shift-preview")).to_have_text("9 scheduled hours")
    page.get_by_role("button", name="Save shift", exact=True).click()
    expect(page.locator("#schedule-totals")).to_contain_text("25.00")
    page.get_by_label("Show", exact=True).select_option(str(employee_id))
    page.get_by_role("button", name="Apply", exact=True).click()
    page.once("dialog", lambda dialog: dialog.dismiss())
    page.locator(f'[data-shift-id="{friday_id}"]').get_by_role(
        "button", name="Cancel shift", exact=True
    ).click()
    expect(page.locator(".scheduled-card")).to_have_count(3)
    page.once("dialog", lambda dialog: dialog.accept())
    page.locator(f'[data-shift-id="{friday_id}"]').get_by_role(
        "button", name="Cancel shift", exact=True
    ).click()
    expect(page.locator(".scheduled-card")).to_have_count(2)
    expect(page.locator("#schedule-totals")).to_contain_text("17.00")
    assert "week=" + week.isoformat() in page.url and "staff=" in page.url
    with app.app_context():
        assert db.session.get(ScheduledShift, friday_id).cancelled
        assert db.session.get(ScheduledShift, monday_id).notes == "Opening crew"
    completed.append(
        "Simple builder: day buttons, multiple shifts, employee filter, hour totals and edits"
    )
    page.locator(f'[data-schedule-day="{week.isoformat()}"]').click()
    page.get_by_label("Employee", exact=True).select_option(str(employee_id))
    page.get_by_label("End time", exact=True).fill("17:00")
    page.get_by_text("Add a note", exact=True).click()
    page.get_by_label("Notes (optional)").fill("Conflict draft")
    rejected_submit()
    expect(page.locator("#schedule-error")).to_contain_text("already has a shift")
    expect(page.get_by_label("Notes (optional)")).to_have_value("Conflict draft")
    page.locator(f'[data-schedule-day="{thursday.isoformat()}"]').click()
    page.get_by_label("Notes (optional)").fill("Keep time off draft")
    rejected_submit()
    expect(page.locator("#schedule-error")).to_contain_text("approved time off")
    expect(page.get_by_label("Notes (optional)")).to_have_value("Keep time off draft")
    expect(page.locator("#schedule-totals")).to_contain_text("17.00")
    capture(page, "schedule-conflict")
    completed.append(
        "Cancel confirmation and preserved week; overlap/time-off errors keep the draft"
    )
    page.goto(origin + "/schedule?week=2026-10-26&on=2026-10-31")
    page.get_by_label("Start time", exact=True).fill("22:00")
    page.get_by_label("End time", exact=True).fill("06:00")
    expect(page.locator("#shift-preview")).to_have_text("9 scheduled hours · Ends Sun, Nov 1")
    page.goto(origin + "/schedule?week=2026-03-02&on=2026-03-08")
    page.get_by_label("Start time", exact=True).fill("01:30")
    page.get_by_label("End time", exact=True).fill("03:30")
    expect(page.locator("#shift-preview")).to_have_text("1 scheduled hour")
    page.get_by_label("Start time", exact=True).fill("02:30")
    expect(page.locator("#shift-preview")).to_have_text(
        "Choose valid times outside the clock change."
    )
    assert not page.get_by_label("End time", exact=True).evaluate("input => input.checkValidity()")
    page.goto(origin + "/schedule?week=" + week.isoformat() + "&on=" + thursday.isoformat())
    page.get_by_label("Employee", exact=True).select_option(str(employee_id))
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    expect(page.get_by_role("button", name="Save shift", exact=True)).to_be_visible()
    capture(page, "schedule-builder-mobile")
    completed.append(
        "Store-zone DST duration preview and mobile schedule form without page overflow"
    )
    context = browser.new_context()
    try:
        employee_page = context.new_page()
        employee_page.on("pageerror", lambda error: errors.append(str(error)))
        employee_page.goto(origin + "/login")
        employee_page.get_by_label("Email or Username", exact=True).fill("demo.employee")
        employee_page.get_by_label("Password", exact=True).first.fill(credentials["demo.employee"])
        employee_page.get_by_role("button", name="Sign In", exact=True).click()
        employee_page.goto(
            origin + "/schedule?week=" + week.isoformat() + "&staff=" + str(employee_id)
        )
        expect(employee_page.get_by_text("Opening crew", exact=True)).to_have_count(0)
        expect(employee_page.locator(".schedule-builder")).to_have_count(0)
        expect(employee_page.locator(".schedule-time-off")).to_have_count(0)
        completed.append(
            "Employee schedule hides other staff's shifts, time off and builder controls"
        )
    finally:
        context.close()
