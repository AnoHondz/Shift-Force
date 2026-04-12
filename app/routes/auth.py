from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_user, logout_user
from flask_login import login_required
from sqlalchemy import or_

from ..extensions import db, login_manager
from ..models import Employee, Role, User, create_audit_log


auth_bp = Blueprint("auth", __name__)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def get_dashboard_endpoint(user):
    if user and user.has_admin_access():
        return "employees.dashboard"
    return "employees.employee_dashboard"


def get_next_url(default_endpoint="employees.dashboard"):
    next_url = request.args.get("next") or request.form.get("next")
    if next_url and next_url.startswith("/"):
        return next_url
    return url_for(default_endpoint)


def _auth_template_context(**kwargs):
    context = {
        "active_page": "login",
        "next_url": get_next_url(),
        "active_tab": "signin",
        "login_error": None,
        "signup_errors": {},
        "signup_values": {},
    }
    context.update(kwargs)
    return context


def _split_full_name(full_name: str):
    parts = [part for part in full_name.split() if part]
    if len(parts) < 2:
        return None, None
    first_name = parts[0]
    last_name = " ".join(parts[1:])
    return first_name, last_name


@auth_bp.route("/signup", methods=("GET", "POST"))
def signup():
    if current_user.is_authenticated:
        return redirect(url_for(get_dashboard_endpoint(current_user)))
    if request.method == "POST":
        full_name = request.form.get("full_name", "").strip()
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        signup_values = {
            "full_name": full_name,
            "username": username,
            "email": email,
        }
        signup_errors = {}

        first_name, last_name = _split_full_name(full_name)
        if not first_name or not last_name:
            signup_errors["full_name"] = "Enter your full name (first and last)."
        if not username:
            signup_errors["username"] = "Username is required."
        elif User.query.filter_by(username=username).first():
            signup_errors["username"] = "That username is already taken."
        if not email:
            signup_errors["email"] = "Email is required."
        elif Employee.query.filter_by(email=email).first():
            existing_employee = Employee.query.filter_by(email=email).first()
            if existing_employee.user:
                signup_errors["email"] = "That email is already associated with another account."
        if len(password) < 8:
            signup_errors["password"] = "Password must be at least 8 characters."
        if password != confirm_password:
            signup_errors["confirm_password"] = "Passwords do not match."

        if not signup_errors:
            employee = Employee(
                first_name=first_name,
                last_name=last_name,
                email=email,
                job_title="Employee",
                department_id=None,
                hourly_rate=25.0,
                overtime_multiplier=1.5,
                date_hired=date.today(),
            )
            db.session.add(employee)
            db.session.flush()

            user = User(username=username, role=Role.EMPLOYEE, employee_id=employee.id)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()

            flash("Account created successfully. Please sign in.", "success")
            return redirect(url_for("auth.login"))

        flash("Please correct the highlighted fields and try again.", "error")
        return render_template(
            "login.html",
            **_auth_template_context(
                active_tab="signup",
                signup_errors=signup_errors,
                signup_values=signup_values,
            ),
        )

    return render_template("login.html", **_auth_template_context(active_tab="signup"))


@auth_bp.route("/login", methods=("GET", "POST"))
def login():
    if current_user.is_authenticated:
        return redirect(url_for(get_dashboard_endpoint(current_user)))

    login_error = None
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        remember_me = request.form.get("remember_me") == "on"
        user = (
            User.query.join(Employee, isouter=True)
            .filter(or_(User.username == username, Employee.email == username.lower()))
            .first()
        )

        if user is None or not user.check_password(password):
            login_error = "Invalid username or password."
            flash(login_error, "error")
            create_audit_log(
                None,
                "login_failed",
                "User",
                None,
                f"Failed login attempt for username/email '{username}'.",
            )
            db.session.commit()
        else:
            login_user(user, remember=remember_me)
            create_audit_log(user, "login_success", "User", str(user.id), f"{user.username} logged in.")
            db.session.commit()
            flash("Welcome back.", "success")
            return redirect(get_next_url(get_dashboard_endpoint(user)))

    return render_template(
        "login.html",
        **_auth_template_context(login_error=login_error, active_tab="signin"),
    )


@auth_bp.route("/logout")
@login_required
def logout():
    create_audit_log(current_user, "logout", "User", str(current_user.id), f"{current_user.username} logged out.")
    db.session.commit()
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("auth.login"))
