"""Exercise staff recovery and MFA without capturing secrets or changing the root page."""

import hashlib
import re
import secrets
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pyotp
from playwright.sync_api import expect

from app.extensions import db
from app.models import AccountReset, Role, User


def verify_account_workflows(page, browser, origin, app, credentials, completed, capture, errors):
    contexts = []

    def new_page(cookies=None):
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        contexts.append(context)
        if cookies:
            context.add_cookies(cookies)
        work = context.new_page()
        work.on("pageerror", lambda error: errors.append(str(error)))
        work.on(
            "response",
            lambda response: (
                errors.append(f"HTTP {response.status}: {response.url}")
                if response.status >= 400
                else None
            ),
        )
        return work

    def sign_in(work):
        work.goto(origin + "/login")
        work.get_by_label("Email or Username", exact=True).fill("demo.manager")
        work.get_by_label("Password", exact=True).first.fill(credentials["demo.manager"])
        work.get_by_role("button", name="Sign In", exact=True).click()

    def staff_form(owner, manager_id):
        return owner.locator("form.surface-card").filter(
            has=owner.locator(f'input[name="user_id"][value="{manager_id}"]')
        )

    try:
        with app.app_context():
            manager = User.query.filter_by(username="demo.manager").one()
            manager_id = manager.id
            manager_employee_id = manager.employee_id
            assert manager.role == Role.MANAGER and not manager.mfa_secret
            admin_token = User.query.filter_by(username="demo.admin").one().auth_token

        owner = new_page(page.context.cookies())
        previous_manager = new_page()
        sign_in(previous_manager)
        expect(previous_manager).to_have_url(origin + "/dashboard")
        owner.goto(origin + "/account/staff")
        form = staff_form(owner, manager_id)
        expect(form.get_by_label("Access", exact=True)).to_have_value(Role.MANAGER)
        capture(owner, "staff-access")
        owner.set_viewport_size({"width": 390, "height": 844})
        assert owner.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        capture(owner, "staff-access-mobile")
        owner.set_viewport_size({"width": 1440, "height": 1000})
        form.get_by_label("Access", exact=True).select_option(Role.MANAGER)
        form.get_by_label("Your password", exact=True).fill(credentials["demo.admin"])
        form.get_by_role("button", name="Save access", exact=True).click()
        expect(
            owner.get_by_text("Staff access updated. Existing sign-ins were removed.")
        ).to_be_visible()
        previous_manager.goto(origin + "/manage")
        expect(previous_manager).to_have_url(re.compile(re.escape(origin + "/login") + r"\?next="))
        completed.append(
            "Staff access changes retain the manager role and revoke previous sign-ins"
        )

        form = staff_form(owner, manager_id)
        form.get_by_label("Your password", exact=True).fill(credentials["demo.admin"])
        form.get_by_role("button", name="Create recovery link", exact=True).click()
        reset_url = owner.get_by_label("Recovery link", exact=True).input_value()
        parsed = urlsplit(reset_url)
        assert parsed.scheme + "://" + parsed.netloc == origin
        assert parsed.path == "/account/reset"
        token = parse_qs(parsed.query)["token"][0]
        with app.app_context():
            record = AccountReset.query.filter_by(
                token_hash=hashlib.sha256(token.encode()).hexdigest()
            ).one()
            assert record.user_id == manager_id and record.consumed_at is None
            assert (
                timedelta(minutes=29)
                < record.expires_at - record.created_at
                <= timedelta(minutes=30)
            )
        manager_page = new_page()
        manager_page.goto(reset_url)
        password = secrets.token_urlsafe(20)
        manager_page.get_by_label("New password", exact=True).fill(password)
        manager_page.get_by_label("Confirm new password", exact=True).fill(password)
        manager_page.get_by_role("button", name="Save password", exact=True).click()
        expect(manager_page).to_have_url(origin + "/login")
        credentials["demo.manager"] = password
        sign_in(manager_page)
        expect(manager_page).to_have_url(origin + "/dashboard")
        with app.app_context():
            assert db.session.get(AccountReset, record.id).consumed_at is not None
        # Remove the recovery link from the owner page before any further capture.
        owner.goto(origin + "/account/staff")
        expect(owner.get_by_label("Recovery link", exact=True)).to_have_count(0)
        completed.append(
            "Owner-issued 30-minute password recovery is redeemed through the staff browser"
        )

        manager_page.goto(origin + "/account/security")
        manager_page.get_by_label("Your password", exact=True).fill(password)
        manager_page.get_by_role("button", name="Set up authenticator", exact=True).click()
        expect(manager_page.get_by_role("img", name="Authenticator setup QR code")).to_be_visible()
        manager_page.get_by_text("Enter the setup key manually", exact=True).click()
        secret = manager_page.locator(".today-workspace code").inner_text()
        manager_page.get_by_label("Your password", exact=True).fill(password)
        manager_page.get_by_label("Authenticator code", exact=True).fill(pyotp.TOTP(secret).now())
        manager_page.get_by_role("button", name="Enable two-step verification", exact=True).click()
        expect(
            manager_page.get_by_role("heading", name="Save your recovery codes", exact=True)
        ).to_be_visible()
        codes = re.findall(r"\b[a-f0-9]{16}\b", manager_page.locator("pre").inner_text())
        assert len(codes) == len(set(codes)) == 10
        manager_page.goto(origin + "/account/security")
        expect(
            manager_page.get_by_role("heading", name="Save your recovery codes", exact=True)
        ).to_have_count(0)
        expect(manager_page.locator(".today-workspace code")).to_have_count(0)
        expect(manager_page.get_by_role("img", name="Authenticator setup QR code")).to_have_count(0)
        expect(
            manager_page.get_by_text(
                "Your authenticator is enabled. Keep your recovery codes safe."
            )
        ).to_be_visible()
        capture(manager_page, "account-security")
        manager_page.set_viewport_size({"width": 390, "height": 844})
        assert manager_page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        capture(manager_page, "account-security-mobile")
        manager_page.set_viewport_size({"width": 1440, "height": 1000})
        manager_page.get_by_role("button", name="Sign out", exact=True).click()
        sign_in(manager_page)
        expect(manager_page).to_have_url(origin + "/account/verify")
        manager_page.get_by_label("Verification code", exact=True).fill(codes[0])
        manager_page.get_by_role("button", name="Verify sign-in", exact=True).click()
        expect(manager_page).to_have_url(origin + "/dashboard")
        manager_page.get_by_role("button", name="Sign out", exact=True).click()
        sign_in(manager_page)
        expect(manager_page).to_have_url(origin + "/account/verify")
        manager_page.get_by_label("Verification code", exact=True).fill(codes[0])
        manager_page.get_by_role("button", name="Verify sign-in", exact=True).click()
        expect(manager_page.locator("[role='alert']")).to_contain_text("invalid or already used")
        expect(manager_page).to_have_url(origin + "/account/verify")
        manager_page.get_by_label("Verification code", exact=True).fill(codes[1])
        manager_page.get_by_role("button", name="Verify sign-in", exact=True).click()
        expect(manager_page).to_have_url(origin + "/dashboard")
        with app.app_context():
            manager = db.session.get(User, manager_id)
            assert manager.role == Role.MANAGER and manager.employee_id == manager_employee_id
            assert manager.mfa_secret and manager.mfa_pending_secret is None
            assert secret not in manager.mfa_secret
            assert User.query.filter_by(username="demo.admin").one().auth_token == admin_token
        completed.append(
            "Authenticator enrollment, single-use recovery sign-in, replay denial and mobile account screens"
        )
    finally:
        for context in contexts:
            context.close()
