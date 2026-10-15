from conftest import TEST_PASSWORD

from app.models import Role, User


def test_login_page_renders(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert b"Welcome back" in response.data


def test_signup_page_renders_signup_tab_active(client):
    response = client.get("/signup")
    assert response.status_code == 200
    assert b"Create your account" in response.data
    assert b"Create Account</a>" in response.data
    assert b'auth-tab active" href="/signup"' in response.data


def test_signup_rejects_password_mismatch_and_preserves_values(client):
    response = client.post(
        "/signup",
        data={
            "full_name": "Taylor Jordan",
            "username": "taylorj",
            "email": "taylor@example.com",
            "password": TEST_PASSWORD,
            "confirm_password": "password321",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Passwords do not match" in response.data
    assert b"taylorj" in response.data
    assert b"taylor@example.com" in response.data


def test_signup_creates_employee_accounts_only(client, app):
    response = client.post(
        "/signup",
        data={
            "full_name": "Taylor Jordan",
            "username": "taylor_employee",
            "email": "taylor.employee@example.com",
            "password": TEST_PASSWORD,
            "confirm_password": TEST_PASSWORD,
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]

    with app.app_context():
        created_user = User.query.filter_by(username="taylor_employee").first()
        assert created_user is not None
        assert created_user.role == Role.EMPLOYEE


def test_successful_signup_redirects_to_signin_with_success_flash(client):
    response = client.post(
        "/signup",
        data={
            "full_name": "Morgan Lee",
            "username": "morganlee",
            "email": "morgan.lee@example.com",
            "password": TEST_PASSWORD,
            "confirm_password": TEST_PASSWORD,
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Account created successfully. Please sign in." in response.data
    assert b"Welcome back" in response.data
