"""Run actual browser workflows against a migrated, disposable local database."""

import argparse
import json
import secrets
import tempfile
import threading
from datetime import date, datetime, timedelta
from io import BytesIO
from pathlib import Path
from time import time
from zoneinfo import ZoneInfo

from flask_migrate import upgrade
from PIL import Image, ImageDraw, ImageFont
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from app import create_app
from app.extensions import db
from app.models import Attendance, Employee, PayrollRecord, TeamChannel, User, utcnow


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--screenshots", action="store_true")
    parser.add_argument("--screenshot-dir", type=Path, default=Path("docs/screenshots"))
    options = parser.parse_args()
    screenshots = options.screenshot_dir
    if options.screenshots:
        screenshots.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as directory:

        class Config:
            SECRET_KEY = secrets.token_hex(32)
            SQLALCHEMY_DATABASE_URI = f"sqlite:///{Path(directory).as_posix()}/browser.db"
            RATELIMIT_ENABLED = False
            PERMANENT_SESSION_LIFETIME = timedelta(minutes=30)
            REMEMBER_COOKIE_DURATION = timedelta(days=7)
            REMEMBER_COOKIE_SAMESITE = "Lax"
            PUBLIC_REGISTRATION = True
            WORKFORCE_TIMEZONE = "America/New_York"

        app = create_app(Config)
        with app.app_context():
            upgrade()
        seeded = app.test_cli_runner().invoke(args=["seed-demo"])
        assert seeded.exit_code == 0, "Demo setup failed."
        credentials = dict(
            line.split(": ", 1) for line in seeded.output.splitlines() if line.startswith("demo.")
        )
        server = make_server("127.0.0.1", 0, app, threaded=True)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        completed = []
        browser_errors = []

        def capture(page, name):
            if options.screenshots:
                page.evaluate("window.scrollTo(0, 0)")
                page.screenshot(path=str(screenshots / f"{name}.png"), full_page=True)

        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                page.on("pageerror", lambda error: browser_errors.append(str(error)))
                page.on(
                    "response",
                    lambda response: (
                        browser_errors.append(f"HTTP {response.status}: {response.url}")
                        if response.status >= 400
                        else None
                    ),
                )

                page.goto(origin + "/login")
                expect(page.get_by_text("Welcome back", exact=True)).to_be_visible()
                capture(page, "login")
                page.get_by_label("Email or Username").fill("demo.employee")
                page.get_by_label("Password", exact=True).first.fill(credentials["demo.employee"])
                page.get_by_label("Remember me on this device", exact=True).check()
                page.get_by_role("button", name="Sign In", exact=True).click()
                expect(page).to_have_url(origin + "/employee/dashboard")
                capture(page, "employee-workspace")
                completed.append("Employee sign-in with CSRF")
                remembered = [
                    cookie
                    for cookie in page.context.cookies()
                    if cookie["name"] == "remember_token"
                ]
                assert len(remembered) == 1
                assert remembered[0]["httpOnly"] and remembered[0]["sameSite"] == "Lax"
                assert 604790 < remembered[0]["expires"] - time() <= 604800
                resumed_context = browser.new_context()
                resumed_context.add_cookies(remembered)
                resumed = resumed_context.new_page()
                resumed.on("pageerror", lambda error: browser_errors.append(str(error)))
                assert resumed.goto(origin + "/employee/dashboard").status == 200
                expect(resumed.get_by_role("region", name="Your shift")).to_be_visible()
                resumed_context.close()

                page.get_by_role("region", name="Your shift").get_by_role(
                    "button", name="Clock in", exact=True
                ).click()
                expect(page).to_have_url(origin + "/employee/dashboard")
                expect(page.get_by_text("On shift", exact=True)).to_be_visible()
                with app.app_context():
                    Attendance.query.one().clock_in_at = utcnow() - timedelta(hours=2)
                    db.session.commit()
                page.reload()
                page.get_by_role("region", name="Your shift").get_by_role(
                    "button", name="Clock out", exact=True
                ).click()
                expect(page.get_by_text("Clocked out successfully.", exact=True)).to_be_visible()
                expect(page.get_by_text("Off shift", exact=True)).to_be_visible()
                page.goto(origin + "/attendance/")
                capture(page, "attendance")
                completed.append("Dashboard shift start/end and attendance history")

                page.goto(origin + "/leave/new")
                page.get_by_label("Leave Type").select_option("Vacation")
                start = date.today() + timedelta(days=7)
                page.get_by_label("Start Date").fill(start.isoformat())
                page.get_by_label("End Date").fill((start + timedelta(days=2)).isoformat())
                page.get_by_role("button", name="Submit", exact=False).click()
                expect(page.get_by_text("Leave request submitted.", exact=True)).to_be_visible()
                completed.append("Leave submission")

                page.get_by_role("button", name="Sign out", exact=True).click()
                expect(page).to_have_url(origin + "/login")
                assert not any(
                    cookie["name"] == "remember_token" for cookie in page.context.cookies()
                )
                page.goto(origin + "/employee/dashboard")
                expect(page).to_have_url(origin + "/login?next=/employee/dashboard?")
                expect(
                    page.get_by_label("Remember me on this device", exact=True)
                ).not_to_be_checked()
                completed.append(
                    "Seven-day remembered sign-in restores a new browser session; sign-out clears it"
                )
                page.goto(origin + "/login")
                page.get_by_label("Email or Username").fill("demo.admin")
                page.get_by_label("Password", exact=True).first.fill(credentials["demo.admin"])
                page.get_by_role("button", name="Sign In", exact=True).click()
                expect(page).to_have_url(origin + "/dashboard")
                assert not any(
                    cookie["name"] == "remember_token" for cookie in page.context.cookies()
                )
                assert (
                    next(
                        cookie for cookie in page.context.cookies() if cookie["name"] == "session"
                    )["expires"]
                    == -1
                )
                capture(page, "dashboard")
                page.get_by_role("region", name="Your shift").get_by_role(
                    "button", name="Clock in", exact=True
                ).click()
                expect(page).to_have_url(origin + "/dashboard")
                expect(page.get_by_text("On shift", exact=True)).to_be_visible()
                page.get_by_role("region", name="Your shift").get_by_role(
                    "button", name="Clock out", exact=True
                ).click()
                expect(page.get_by_text("Off shift", exact=True)).to_be_visible()
                completed.append("Logout and administrator sign-in")

                page.get_by_role("navigation", name="Main navigation").get_by_role(
                    "link", name="Manage workplace", exact=True
                ).click()
                expect(page).to_have_url(origin + "/manage")
                capture(page, "manage-workplace")
                page.get_by_role("link", name="Punches", exact=False).click()
                page.get_by_text("Add missed punches", exact=True).click()
                with app.app_context():
                    employee_id = User.query.filter_by(username="demo.employee").one().employee_id
                yesterday = datetime.now(ZoneInfo("America/New_York")).replace(
                    hour=9, minute=0, second=0, microsecond=0
                ) - timedelta(days=1)
                page.get_by_label("Employee", exact=True).select_option(str(employee_id))
                page.get_by_label("Clock in", exact=True).fill(yesterday.strftime("%Y-%m-%dT%H:%M"))
                page.get_by_label("Clock out (leave blank if still working)", exact=True).fill(
                    (yesterday + timedelta(hours=7)).strftime("%Y-%m-%dT%H:%M")
                )
                page.get_by_label("Reason for correction").fill("Forgot to punch yesterday")
                page.get_by_role("button", name="Save punches").click()
                expect(page.get_by_text("Punches saved.", exact=False)).to_be_visible()
                capture(page, "missed-punches")
                with app.app_context():
                    missed_id = (
                        Attendance.query.filter_by(employee_id=employee_id)
                        .order_by(Attendance.id.desc())
                        .first()
                        .id
                    )
                page.goto(origin + f"/manage/punches?edit={missed_id}")
                page.get_by_label("Clock out (leave blank if still working)", exact=True).fill(
                    (yesterday + timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M")
                )
                page.get_by_label("Reason for correction").fill("Correct end time from shift notes")
                page.get_by_role("button", name="Save punches").click()
                with app.app_context():
                    assert db.session.get(Attendance, missed_id).hours_worked == 8
                completed.append("Management entry, missed punches and audited correction")

                page.goto(origin + "/schedule")
                page.get_by_text("Add a shift", exact=True).click()
                tomorrow = yesterday + timedelta(days=2)
                page.get_by_label("Employee", exact=True).select_option(str(employee_id))
                page.get_by_label("Start", exact=True).fill(tomorrow.strftime("%Y-%m-%dT%H:%M"))
                page.get_by_label("End", exact=True).fill(
                    (tomorrow + timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M")
                )
                page.get_by_label("Notes (optional)").fill("Front desk coverage")
                page.get_by_role("button", name="Save shift").click()
                expect(page.get_by_text("Schedule saved.", exact=True)).to_be_visible()
                capture(page, "schedule")
                completed.append("Manager publishes dated shift")

                coworker_context = browser.new_context(viewport={"width": 1440, "height": 1000})
                coworker = coworker_context.new_page()
                coworker.on("pageerror", lambda error: browser_errors.append(str(error)))
                coworker.goto(origin + "/login")
                coworker.get_by_label("Email or Username").fill("demo.employee")
                coworker.get_by_label("Password", exact=True).first.fill(
                    credentials["demo.employee"]
                )
                coworker.get_by_role("button", name="Sign In", exact=True).click()
                expect(coworker.get_by_text("Front desk coverage", exact=True)).to_be_visible()
                capture(coworker, "employee-workspace")
                coworker.goto(origin + "/chat")
                coworker.get_by_label("Message your team").fill("Can someone cover the front desk?")
                coworker.get_by_role("button", name="Send", exact=True).click()
                expect(
                    coworker.get_by_text("Can someone cover the front desk?", exact=True)
                ).to_be_visible()
                page.goto(origin + "/chat")
                expect(
                    page.get_by_text("Can someone cover the front desk?", exact=True)
                ).to_be_visible()
                page.get_by_label("Message your team").fill("I can help. See you tomorrow!")
                page.get_by_role("button", name="Send", exact=True).click()
                expect(
                    coworker.get_by_text("I can help. See you tomorrow!", exact=True)
                ).to_be_visible(timeout=12000)
                capture(page, "chat-general")
                page.locator(".create-channel summary").click()
                page.get_by_label("Channel name", exact=True).fill("shift-swaps")
                page.get_by_label("Description (optional)", exact=True).fill(
                    "Find cover and share shift notes"
                )
                page.get_by_role("button", name="Create channel", exact=True).click()
                with app.app_context():
                    channel_id = TeamChannel.query.filter_by(name="shift-swaps").one().id
                expect(page).to_have_url(origin + f"/chat/{channel_id}")
                coworker.reload()
                coworker.get_by_role("navigation", name="Channels", exact=True).get_by_role(
                    "link", name="shift-swaps", exact=True
                ).click()
                expect(coworker).to_have_url(origin + f"/chat/{channel_id}")
                composer = page.get_by_label("Message your team")
                composer.fill("Great  team")
                composer.evaluate("input => input.setSelectionRange(6, 6)")
                composer.press("ArrowLeft")
                composer.press("ArrowRight")
                page.get_by_role("button", name="Choose emoji", exact=True).click()
                picker = page.get_by_role("dialog", name="Choose emoji", exact=True)
                expect(
                    picker.get_by_role("button", name="grinning face", exact=True)
                ).to_be_visible()
                capture(page, "emoji-picker")
                picker.get_by_label("Search emoji", exact=True).fill("thumbs up")
                picker.get_by_label("Skin tone", exact=True).select_option("4")
                picker.get_by_role(
                    "button", name="thumbs up: medium-dark skin tone", exact=True
                ).click()
                expect(composer).to_have_value("Great 👍🏾 team")
                expect(picker).to_be_hidden()
                page.get_by_role("button", name="Send", exact=True).click()
                expect(coworker.get_by_text("Great 👍🏾 team", exact=True)).to_be_visible(
                    timeout=12000
                )
                page.reload()
                expect(page.get_by_text("Great 👍🏾 team", exact=True)).to_be_visible()
                page.get_by_role("button", name="Choose emoji", exact=True).click()
                picker.get_by_role("button", name="New on iPhone", exact=True).click()
                expect(picker.get_by_role("button", name="orca", exact=True)).to_be_visible()
                picker.get_by_label("Search emoji", exact=True).fill("grinning face")
                picker.get_by_label("Search emoji", exact=True).press("Enter")
                expect(
                    picker.get_by_role("button", name="grinning face", exact=True)
                ).to_be_focused()
                page.keyboard.press("ArrowRight")
                expect(
                    picker.get_by_role("button", name="grinning face with big eyes", exact=True)
                ).to_be_focused()
                page.keyboard.press("Escape")
                expect(picker).to_be_hidden()
                expect(page.get_by_role("button", name="Choose emoji", exact=True)).to_be_focused()
                composer.fill("a" * 2000)
                page.get_by_role("button", name="Choose emoji", exact=True).click()
                picker.get_by_role("button", name="grinning face", exact=True).click()
                expect(
                    picker.get_by_text("Your message is full. Remove some text to add an emoji.")
                ).to_be_visible()
                expect(composer).to_have_value("a" * 2000)
                picker.get_by_role("button", name="Close emoji picker", exact=True).click()
                composer.fill("")
                completed.append(
                    "Emoji search, categories, skin tones, caret insertion, keyboard controls, message limit and shared persistence"
                )
                photo = Image.new("RGB", (480, 240), "#292a35")
                drawing = ImageDraw.Draw(photo)
                drawing.rectangle((18, 18, 462, 222), fill="#3d3a50")
                font = ImageFont.load_default(size=23)
                drawing.text((40, 42), "Tomorrow's handoff", fill="#e8e0ff", font=font)
                drawing.text((40, 102), "Front desk: Taylor", fill="#c3badb", font=font)
                drawing.text((40, 146), "9 AM - 5 PM", fill="#c3badb", font=font)
                buffer = BytesIO()
                photo.save(buffer, format="PNG")
                page.locator("#chat-picture").set_input_files(
                    {"name": "handoff.png", "mimeType": "image/png", "buffer": buffer.getvalue()}
                )
                expect(page.get_by_alt_text("Picture ready to share")).to_be_visible()
                page.get_by_label("Message your team").fill("Coverage notes for tomorrow")
                page.get_by_role("button", name="Send", exact=True).click()
                expect(page.locator(".chat-picture")).to_be_visible()
                expect(coworker.locator(".chat-picture")).to_be_visible(timeout=12000)
                assert coworker.locator(".chat-picture").evaluate("image => image.naturalWidth > 0")
                page.reload()
                expect(page.locator(".chat-picture")).to_be_visible()
                assert page.evaluate("document.documentElement.scrollHeight <= window.innerHeight")
                capture(page, "team-chat")
                coworker.goto(origin + "/chat")
                expect(
                    coworker.get_by_text("Coverage notes for tomorrow", exact=True)
                ).to_have_count(0)
                capture(coworker, "chat-general")
                coworker.goto(origin + f"/chat/{channel_id}")
                completed.append(
                    "Admin channel creation, isolated conversations, preview and protected picture sharing"
                )
                page.goto(origin + "/dashboard")
                capture(page, "dashboard")
                coworker_context.close()
                completed.append("Coworker chat across separate signed-in sessions with polling")

                page.goto(origin + "/employees/new")
                for label, value in {
                    "First Name": "Sam",
                    "Last Name": "Taylor",
                    "Email": "sam.taylor@example.com",
                    "Job Title": "Software Engineer",
                    "Hourly Rate": "32.50",
                    "Overtime Multiplier": "1.50",
                }.items():
                    page.get_by_label(label, exact=True).fill(value)
                page.get_by_role("button", name="Save Employee").click()
                expect(page.get_by_text("Employee added successfully.", exact=True)).to_be_visible()
                page.get_by_label("Search", exact=True).fill("Sam")
                page.get_by_role("button", name="Apply Filters").click()
                expect(page.get_by_text("Sam Taylor", exact=True).first).to_be_visible()
                with app.app_context():
                    sam_id = Employee.query.filter_by(email="sam.taylor@example.com").one().id
                page.goto(origin + f"/employees/{sam_id}/edit")
                page.get_by_label("First Name", exact=True).fill("Samuel")
                page.get_by_role("button", name="Save Employee").click()
                expect(
                    page.get_by_text("Employee updated successfully.", exact=True)
                ).to_be_visible()
                page.goto(origin + f"/employees/{sam_id}")
                expect(page.get_by_role("heading", name="Samuel Taylor").first).to_be_visible()
                capture(page, "employee-profile")
                page.goto(origin + "/employees")
                capture(page, "employees")
                completed.append("Employee create, search, edit and profile")

                with app.app_context():
                    employee_id = User.query.filter_by(username="demo.employee").one().employee_id
                    employee_record = (
                        PayrollRecord.query.filter_by(employee_id=employee_id)
                        .filter(PayrollRecord.overtime_hours > 0)
                        .order_by(PayrollRecord.work_date.desc())
                        .first()
                    )
                    assert employee_record.overtime_hours == 5
                    assert employee_record.total_pay == 345
                    record_id = employee_record.id
                page.goto(origin + f"/payroll?employee_id={employee_id}")
                page.locator(f'form[action="/payroll/{record_id}/review"]').filter(
                    has=page.locator('input[value="approve"]')
                ).get_by_role("button", name="Approve").click()
                expect(page.get_by_text("Entry marked as approved.", exact=True)).to_be_visible()
                capture(page, "payroll")
                completed.append("Weekly overtime and payroll approval")

                page.goto(origin + "/leave/")
                page.get_by_role("button", name="Approve", exact=True).first.click()
                expect(page.get_by_text("Leave request approved.", exact=True)).to_be_visible()
                capture(page, "leave")
                completed.append("Leave approval")

                for path, name in [
                    ("/notifications/", "notifications"),
                    ("/reports/", "reports"),
                    ("/audit/logs", "audit"),
                ]:
                    response = page.goto(origin + path)
                    assert response.status == 200
                    capture(page, name)
                page.goto(origin + "/reports/")
                with page.expect_download() as csv:
                    page.get_by_role("link", name="Export CSV", exact=True).click()
                assert Path(csv.value.path()).read_text().startswith("Employee")
                with page.expect_download() as pdf:
                    page.get_by_role("link", name="Export PDF", exact=True).click()
                assert Path(pdf.value.path()).read_bytes().startswith(b"%PDF")
                completed.append("Notifications, audit trail, CSV and PDF downloads")

                page.set_viewport_size({"width": 390, "height": 844})
                for path, name in [
                    ("/dashboard", "dashboard-mobile"),
                    ("/employees", "employees-mobile"),
                    ("/payroll", "payroll-mobile"),
                    ("/schedule", "schedule-mobile"),
                    (f"/chat/{channel_id}", "team-chat-mobile"),
                    ("/manage", "manage-workplace-mobile"),
                ]:
                    page.goto(origin + path)
                    assert page.evaluate(
                        "document.documentElement.scrollWidth <= window.innerWidth"
                    ), path
                    if path == "/payroll":
                        region = page.get_by_role("region", name="Payroll entries")
                        assert region.evaluate("el => el.scrollWidth > el.clientWidth")
                        assert region.locator("tbody td.text-nowrap").first.evaluate(
                            """el => {
                                const text = document.createRange();
                                text.selectNodeContents(el);
                                return text.getClientRects().length === 1;
                            }"""
                        ), "Payroll dates should remain readable on one line."
                        region.evaluate("el => el.scrollLeft = el.scrollWidth")
                        assert region.evaluate("el => el.scrollLeft > 0")
                        region.evaluate("el => el.scrollLeft = 0")
                    capture(page, name)
                    if path == f"/chat/{channel_id}":
                        page.get_by_role("button", name="Choose emoji", exact=True).click()
                        mobile_picker = page.get_by_role("dialog", name="Choose emoji", exact=True)
                        expect(
                            mobile_picker.get_by_role("button", name="grinning face", exact=True)
                        ).to_be_visible()
                        box = mobile_picker.bounding_box()
                        assert box["x"] >= 0 and box["x"] + box["width"] <= 390
                        assert page.evaluate(
                            "document.documentElement.scrollWidth <= window.innerWidth"
                        )
                        capture(page, "emoji-picker-mobile")
                        mobile_picker.get_by_role(
                            "button", name="Close emoji picker", exact=True
                        ).click()
                completed.append(
                    "Mobile home, directory, payroll, schedule, chat and management: no page overflow"
                )
                assert browser_errors == [], browser_errors
                browser.close()
        finally:
            server.shutdown()
            thread.join(timeout=5)
            with app.app_context():
                db.session.remove()
                db.engine.dispose()
        print(json.dumps({"completed": completed, "browser_errors": browser_errors}, indent=2))


if __name__ == "__main__":
    main()
