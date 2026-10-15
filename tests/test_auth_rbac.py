from conftest import TEST_PASSWORD

from app.extensions import db
from app.models import PayrollRecord


def login(client, username, password=TEST_PASSWORD):
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )


def test_login_and_logout_flow(client, users):
    response = login(client, "admin_user")
    assert response.status_code == 302
    assert "/dashboard" in response.headers["Location"]

    logout_response = client.post("/logout", follow_redirects=False)
    assert logout_response.status_code == 302
    assert "/login" in logout_response.headers["Location"]


def test_employee_blocked_from_admin_pages(client, users):
    login(client, "employee_user")
    response = client.get("/employees", follow_redirects=False)
    assert response.status_code == 302
    assert "/employee/dashboard" in response.headers["Location"]


def test_admin_can_access_admin_pages(client, users):
    login(client, "admin_user")
    response = client.get("/employees")
    assert response.status_code == 200


def test_payroll_review_requires_admin(client, app, users, payroll_record):
    login(client, "employee_user")
    response = client.post(
        f"/payroll/{payroll_record}/review", data={"action": "approve"}, follow_redirects=False
    )
    assert response.status_code == 302
    assert "/employee/dashboard" in response.headers["Location"]

    client.post("/logout", follow_redirects=False)
    login(client, "admin_user")
    with app.app_context():
        version = db.session.get(PayrollRecord, payroll_record).version_id
    response = client.post(
        f"/payroll/{payroll_record}/review",
        data={"action": "approve", "version_id": version},
        follow_redirects=False,
    )
    assert response.status_code == 302

    with app.app_context():
        updated = db.session.get(PayrollRecord, payroll_record)
        assert updated.status == "Approved"
