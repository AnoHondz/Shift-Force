"""Exercise the researched planning and time-review workflows in real Chromium."""

import secrets
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from playwright.sync_api import expect

from app.extensions import db
from app.models import Attendance, Employee, PayrollRecord, Role, User
from app.scheduling import monday


def verify_planning_upgrade(page, browser, origin, app, completed, capture, errors):
    page.set_viewport_size(dict(width=1440, height=1000))
    zone = ZoneInfo("America/New_York")
    today = datetime.now(zone).date()
    week = monday(today) + timedelta(days=56)
    past = monday(today) - timedelta(days=7)
    password = secrets.token_urlsafe(24)
    with app.app_context():
        people = []
        for username, first in [("planning.worker", "Planning"), ("planning.cover", "Coverage")]:
            employee = Employee(
                first_name=first,
                last_name="Colleague",
                email=username + "@example.com",
                hourly_rate=18,
                overtime_multiplier=1.5,
            )
            db.session.add(employee)
            db.session.flush()
            user = User(username=username, role=Role.EMPLOYEE, employee_id=employee.id)
            user.set_password(password)
            db.session.add(user)
            people.append(employee.id)
        db.session.commit()
    worker_id, _ = people
    worker, cover = browser.new_page(), browser.new_page()
    for separate in [worker, cover]:
        separate.on("pageerror", lambda error: errors.append(str(error)))
        separate.on(
            "response",
            lambda response: (
                errors.append(f"HTTP {response.status}: {response.url}")
                if response.status >= 400
                else None
            ),
        )
    try:
        for separate, username in [(worker, "planning.worker"), (cover, "planning.cover")]:
            separate.goto(origin + "/login")
            separate.get_by_label("Email or Username").fill(username)
            separate.get_by_label("Password", exact=True).first.fill(password)
            separate.get_by_role("button", name="Sign In", exact=True).click()
            expect(separate).to_have_url(origin + "/employee/dashboard")
        worker.goto(origin + "/schedule/availability")
        worker.get_by_label("Monday availability").select_option("hours")
        worker.locator("#start-0").fill("09:00")
        worker.locator("#end-0").fill("18:00")
        worker.get_by_label("Tuesday availability").select_option("off")
        worker.get_by_role("button", name="Save availability", exact=True).click()
        capture(worker, "availability")
        worker.set_viewport_size(dict(width=390, height=844))
        assert worker.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        capture(worker, "availability-mobile")
        worker.set_viewport_size(dict(width=1280, height=900))
        completed.append("Staff availability: real self-service form and mobile layout")

        page.goto(origin + "/schedule?week=" + week.isoformat())
        page.get_by_label("Employee", exact=True).select_option(str(worker_id))
        page.get_by_label("Mon", exact=True).check()
        page.get_by_text("Add a note", exact=True).click()
        page.get_by_label("Notes (optional)").fill("Private roster preview")
        page.get_by_role("button", name="Save draft", exact=True).click()
        expect(page.get_by_text("1 private draft", exact=True)).to_be_visible()
        capture(page, "schedule-drafts")
        worker.goto(origin + "/schedule?week=" + week.isoformat())
        expect(worker.get_by_text("Private roster preview", exact=True)).to_have_count(0)
        page.get_by_role("button", name="Publish 1 draft", exact=True).click()
        worker.reload()
        expect(worker.get_by_text("Private roster preview", exact=True)).to_be_visible()
        completed.append(
            "Private drafts stay hidden until atomic publication through the manager UI"
        )

        worker.get_by_role("button", name="Offer for coverage", exact=True).click()
        expect(worker.get_by_text("Open for coverage", exact=True)).to_be_visible()
        cover.goto(origin + "/schedule/coverage")
        cover.get_by_role("button", name="Request this shift", exact=True).click()
        expect(cover.get_by_text("Awaiting approval", exact=True)).to_be_visible()
        page.goto(origin + "/schedule/coverage")
        capture(page, "coverage-requests")
        page.set_viewport_size(dict(width=390, height=844))
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        capture(page, "coverage-requests-mobile")
        page.set_viewport_size(dict(width=1440, height=1000))
        page.get_by_role("button", name="Approve coverage", exact=True).click()
        cover.goto(origin + "/schedule?week=" + week.isoformat())
        expect(cover.get_by_text("Private roster preview", exact=True)).to_be_visible()
        worker.goto(origin + "/schedule?week=" + week.isoformat())
        expect(worker.get_by_text("Private roster preview", exact=True)).to_have_count(0)
        completed.append(
            "Coworker coverage: offer, request, independent manager approval and reassignment"
        )

        next_week = week + timedelta(days=7)
        page.goto(origin + "/schedule?week=" + next_week.isoformat())
        page.get_by_text("Week tools · copy shifts and check planned cost", exact=True).click()
        page.get_by_role("button", name="Copy previous week", exact=True).click()
        expect(page.get_by_text("1 private draft", exact=True)).to_be_visible()
        page.get_by_text("Week tools · copy shifts and check planned cost", exact=True).click()
        capture(page, "schedule-week-tools")
        page.set_viewport_size(dict(width=390, height=844))
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        capture(page, "schedule-week-tools-mobile")
        page.set_viewport_size(dict(width=1440, height=1000))
        cover.goto(origin + "/schedule?week=" + next_week.isoformat())
        expect(cover.locator(".scheduled-card")).to_have_count(0)
        page.get_by_role("button", name="Publish 1 draft", exact=True).click()
        completed.append(
            "Copy previous week preserves a private review step; planned cost and mobile tools"
        )

        with app.app_context():
            for offset in range(2):
                day = past + timedelta(days=offset)
                start = (
                    datetime.combine(day, time(9), zone)
                    .astimezone(timezone.utc)
                    .replace(tzinfo=None)
                )
                row = Attendance(employee_id=worker_id, date=day, clock_in_at=start)
                row.complete_shift(start + timedelta(hours=8))
                db.session.add(row)
            db.session.commit()
        page.goto(
            origin
            + "/payroll/clock-hours?employee_id="
            + str(worker_id)
            + "&week="
            + past.isoformat()
        )
        expect(page.get_by_text("16.00 recorded hours", exact=True)).to_be_visible()
        capture(page, "clock-hours-review")
        page.set_viewport_size(dict(width=390, height=844))
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        capture(page, "clock-hours-review-mobile")
        page.set_viewport_size(dict(width=1440, height=1000))
        page.locator('[name="confirmed"]').check()
        page.get_by_role("button", name="Prepare pending timesheets", exact=True).click()
        expect(page.get_by_text("Prepared 2 pending entries.", exact=True)).to_be_visible()
        with app.app_context():
            rows = PayrollRecord.query.filter_by(employee_id=worker_id).all()
            assert len(rows) == 2 and all(
                row.status == "Pending" and row.attendance_fingerprint for row in rows
            )
        worker.goto(origin + "/payroll/clock-hours?week=" + past.isoformat())
        expect(worker.get_by_text("16.00 recorded hours", exact=True)).to_be_visible()
        expect(
            worker.get_by_role("button", name="Prepare pending timesheets", exact=True)
        ).to_have_count(0)
        completed.append(
            "Actual clock-hour review prepares pending sourced timesheets without employee approval access"
        )
    finally:
        worker.context.close()
        cover.context.close()
