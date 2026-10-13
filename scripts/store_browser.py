"""Exercise actual store provisioning, owner entry and staff invitations in Chromium."""

import secrets

from playwright.sync_api import expect

from app.models import Employee


def verify_stores(page, browser, origin, app, completed, capture, browser_errors, photo):
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.get_by_role("button", name="Sign out", exact=True).click()
    page.goto(origin + "/owner/signup")
    owner_password = secrets.token_urlsafe(20)
    for label, value in {
        "Full Name": "Casey Williams",
        "Username": "casey.owner",
        "Email": "casey@example.com",
        "Confirm Password": owner_password,
    }.items():
        page.get_by_label(label, exact=True).fill(value)
    page.get_by_label("Password", exact=True).last.fill(owner_password)
    page.get_by_role("button", name="Create Account", exact=True).click()
    page.get_by_label("Email or Username", exact=True).fill("casey.owner")
    page.get_by_label("Password", exact=True).first.fill(owner_password)
    page.get_by_role("button", name="Sign In", exact=True).click()
    expect(page).to_have_url(origin + "/stores")
    expect(page.get_by_role("link", name="Team chat", exact=True)).to_have_count(0)
    assert page.request.get(origin + "/api/employees").status == 403
    completed.append("Owner signup reaches My stores and cannot read the shared hub workplace")
    for index, location in enumerate(["Broad Street", "Cox Street"]):
        if index:
            page.locator(".add-store > summary").click()
        page.get_by_label("Business name", exact=True).fill("McDonald's")
        page.get_by_label("Location", exact=True).fill(location)
        page.get_by_role("button", name="Create store", exact=True).click()
        expect(page.get_by_role("button", name="Open " + location, exact=True)).to_be_visible()
    capture(page, "my-stores")
    page.get_by_role("button", name="Open Broad Street", exact=True).click()
    broad = "/s/mcdonalds-broad-street"
    cox = "/s/mcdonalds-cox-street"
    expect(page).to_have_url(origin + broad + "/dashboard")
    expect(page.locator(".brand-caption")).to_have_text("Broad Street")
    page.goto(origin + broad + "/employees/new")
    for label, value in {
        "First Name": "Jordan",
        "Last Name": "Brooks",
        "Email": "jordan@example.com",
        "Job Title": "Crew member",
        "Hourly Rate": "24.50",
        "Overtime Multiplier": "1.50",
    }.items():
        page.get_by_label(label, exact=True).fill(value)
    page.get_by_role("button", name="Save Employee", exact=True).click()
    child = app.extensions["stores"].get_app("mcdonalds-broad-street")
    with child.app_context():
        employee_id = Employee.query.filter_by(email="jordan@example.com").one().id
    page.goto(origin + broad + f"/employees/{employee_id}")
    page.get_by_role("button", name="Invite to this store", exact=True).click()
    invitation = page.get_by_label("Invitation link", exact=True).input_value()
    worker_context = browser.new_context()
    visitor_context = browser.new_context()
    try:
        worker = worker_context.new_page()
        worker.on("pageerror", lambda error: browser_errors.append(str(error)))
        worker.goto(invitation)
        expect(worker.get_by_label("Full Name", exact=True)).to_have_value("Jordan Brooks")
        password = secrets.token_urlsafe(20)
        worker.get_by_label("Username", exact=True).fill("jordan.brooks")
        worker.get_by_label("Password", exact=True).last.fill(password)
        worker.get_by_label("Confirm Password", exact=True).fill(password)
        worker.get_by_role("button", name="Create Account", exact=True).click()
        expect(worker).to_have_url(origin + broad + "/login")
        worker.get_by_label("Email or Username", exact=True).fill("jordan.brooks")
        worker.get_by_label("Password", exact=True).first.fill(password)
        worker.get_by_label("Remember me on this device", exact=True).check()
        worker.get_by_role("button", name="Sign In", exact=True).click()
        expect(worker).to_have_url(origin + broad + "/employee/dashboard")
        worker.goto(origin + broad + "/chat")
        worker.get_by_label("Message your team").fill("Broad Street handoff notes")
        worker.locator("#chat-picture").set_input_files(
            {"name": "store-handoff.png", "mimeType": "image/png", "buffer": photo}
        )
        worker.get_by_role("button", name="Send", exact=True).click()
        expect(worker.get_by_text("Broad Street handoff notes", exact=True)).to_be_visible()
        page.goto(origin + broad + "/chat")
        expect(page.get_by_text("Broad Street handoff notes", exact=True)).to_be_visible()
        expect(page.locator(".chat-picture")).to_be_visible()
        picture_url = page.locator(".chat-picture").get_attribute("src")
        assert picture_url.startswith(broad + "/chat/pictures/")
        capture(page, "store-chat")
        page.get_by_role("link", name="My stores", exact=True).click()
        page.get_by_role("button", name="Open Cox Street", exact=True).click()
        expect(page.locator(".brand-caption")).to_have_text("Cox Street")
        page.goto(origin + cox + "/chat")
        expect(page.get_by_text("Broad Street handoff notes", exact=True)).to_have_count(0)
        expect(page.locator(".chat-picture")).to_have_count(0)
        page.get_by_label("Message your team").fill("Cox Street opening team")
        page.get_by_role("button", name="Send", exact=True).click()
        expect(page.get_by_text("Cox Street opening team", exact=True)).to_be_visible()
        worker.goto(origin + cox + "/chat")
        expect(worker).to_have_url(origin + cox + "/login?next=/chat?")
        assert worker.request.get(origin + cox + "/api/chat/messages").status == 401
        assert (
            worker.request.get(origin + picture_url.replace(broad, cox, 1), max_redirects=0).status
            == 302
        )
        worker.goto(origin + broad + "/chat")
        expect(worker.get_by_text("Broad Street handoff notes", exact=True)).to_be_visible()
        expect(worker.get_by_text("Cox Street opening team", exact=True)).to_have_count(0)
        worker.get_by_role("button", name="Choose emoji", exact=True).click()
        expect(
            worker.get_by_role("dialog").get_by_role("button", name="grinning face", exact=True)
        ).to_be_visible()
        worker.keyboard.press("Escape")
        completed.append(
            "Owner creates and switches between separate store databases and scoped sessions"
        )
        completed.append(
            "Invited coworker joins one store, shares a protected picture and emoji, and cannot access another location"
        )
        visitor = visitor_context.new_page()
        visitor.on("pageerror", lambda error: browser_errors.append(str(error)))
        visitor.goto(origin + "/stores/find")
        visitor.get_by_label("Search stores", exact=True).fill("McDonald's Broad Street")
        visitor.get_by_role("button", name="Find store", exact=True).click()
        expect(visitor.locator(".store-result")).to_have_count(1)
        capture(visitor, "find-store")
        visitor.locator(".store-result").click()
        expect(visitor).to_have_url(origin + broad + "/login")
        expect(visitor.get_by_role("heading", name="McDonald's", exact=True)).to_be_visible()
        capture(visitor, "store-login")
        page.get_by_role("link", name="My stores", exact=True).click()
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        capture(page, "my-stores-mobile")
        visitor.set_viewport_size({"width": 390, "height": 844})
        assert visitor.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        visitor.goto(origin + "/stores/find")
        assert visitor.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        completed.append("Store search, location-branded sign-in and mobile owner/store layouts")
    finally:
        worker_context.close()
        visitor_context.close()
