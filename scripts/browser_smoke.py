"""Run actual browser workflows against a migrated, disposable local database."""

import argparse
import json
import secrets
import tempfile
import threading
from datetime import date, timedelta
from pathlib import Path

from flask_migrate import upgrade
from playwright.sync_api import expect, sync_playwright
from werkzeug.serving import make_server

from app import create_app
from app.extensions import db
from app.models import Attendance, Employee, PayrollRecord, User, utcnow


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
                page.get_by_role("button", name="Sign In", exact=True).click()
                expect(page).to_have_url(origin + "/employee/dashboard")
                capture(page, "employee-workspace")
                completed.append("Employee sign-in with CSRF")

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
                page.get_by_label("Email or Username").fill("demo.admin")
                page.get_by_label("Password", exact=True).first.fill(credentials["demo.admin"])
                page.get_by_role("button", name="Sign In", exact=True).click()
                expect(page).to_have_url(origin + "/dashboard")
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
                completed.append(
                    "Mobile dashboard, directory and payroll: no horizontal page overflow"
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
