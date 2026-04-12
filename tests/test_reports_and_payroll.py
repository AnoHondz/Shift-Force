from app.models import PayrollRecord


def login(client, username, password="password123"):
    return client.post(
        "/login",
        data={"username": username, "password": password},
        follow_redirects=False,
    )


def test_payroll_calculate_overtime():
    regular, overtime, total = PayrollRecord.calculate(45, 20, 1.5)
    assert regular == 40
    assert overtime == 5
    assert total == 950


def test_report_exports_are_protected_and_work(client, app, users):
    login(client, "employee_user")
    denied = client.get("/reports/export.csv", follow_redirects=False)
    assert denied.status_code == 302

    client.get("/logout", follow_redirects=False)
    login(client, "admin_user")
    csv_response = client.get("/reports/export.csv?period=weekly")
    assert csv_response.status_code == 200
    assert csv_response.mimetype == "text/csv"
    assert b"Employee" in csv_response.data

    pdf_response = client.get("/reports/export.pdf?period=weekly")
    assert pdf_response.status_code == 200
    assert pdf_response.mimetype == "application/pdf"
    assert pdf_response.data.startswith(b"%PDF")


def test_notifications_scope_employee_vs_admin(client, users):
    login(client, "employee_user")
    response = client.get("/notifications/")
    assert response.status_code == 200

    client.get("/logout", follow_redirects=False)
    login(client, "admin_user")
    response = client.get("/notifications/")
    assert response.status_code == 200
