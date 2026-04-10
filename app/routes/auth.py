from datetime import date

from flask import Blueprint, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_user, logout_user

from ..extensions import db, login_manager
from ..models import Department, Employee, Role, User


auth_bp = Blueprint("auth", __name__)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def get_next_url(default_endpoint="employees.dashboard"):
    next_url = request.args.get("next") or request.form.get("next")
    if next_url and next_url.startswith("/"):
        return next_url
    return url_for(default_endpoint)


@auth_bp.route("/signup", methods=("GET", "POST"))
def signup():
    if current_user.is_authenticated:
        return redirect(url_for("employees.dashboard"))

    departments = Department.query.order_by(Department.name).all()
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", Role.EMPLOYEE).strip()
        invite_code = request.form.get("invite_code", "").strip()
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        job_title = request.form.get("job_title", "").strip()
        department_id = request.form.get("department_id", "") or None

        error = None
        if not username:
            error = "Username is required."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif role not in Role.CHOICES:
            error = "Please choose a valid role."
        elif User.query.filter_by(username=username).first():
            error = "That username is already taken."
        elif not first_name or not last_name or not email:
            error = "First name, last name, and email are required."
        elif role == Role.MANAGER and invite_code != current_app.config["MANAGER_SIGNUP_CODE"]:
            error = "Manager signup code is invalid."
        elif role == Role.ADMIN and invite_code != current_app.config["ADMIN_SIGNUP_CODE"]:
            error = "Admin signup code is invalid."
        elif Employee.query.filter_by(email=email).first():
            existing_employee = Employee.query.filter_by(email=email).first()
            if existing_employee.user:
                error = "That email is already associated with another account."

        if error is None:
            if job_title == "":
                job_title = {
                    Role.ADMIN: "Administrator",
                    Role.MANAGER: "Manager",
                    Role.EMPLOYEE: "Employee",
                }.get(role, "Employee")

            employee = Employee(
                first_name=first_name,
                last_name=last_name,
                email=email,
                job_title=job_title,
                department_id=department_id,
                hourly_rate=25.0,
                overtime_multiplier=1.5,
                date_hired=date.today(),
            )
            db.session.add(employee)
            db.session.flush()

            user = User(username=username, role=role, employee_id=employee.id)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()

            flash("Account created successfully. Please log in.", "success")
            return redirect(url_for("auth.login"))

        flash(error, "error")

    return render_template(
        "signup.html",
        departments=departments,
        active_page="signup",
        role_choices=Role.CHOICES,
    )


@auth_bp.route("/login", methods=("GET", "POST"))
def login():
    if current_user.is_authenticated:
        return redirect(url_for("employees.dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()

        if user is None or not user.check_password(password):
            flash("Invalid username or password.", "error")
        else:
            login_user(user)
            flash("Welcome back.", "success")
            return redirect(get_next_url())

    return render_template(
        "login.html",
        next_url=get_next_url(),
        active_page="login",
    )


@auth_bp.route("/logout")
def logout():
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("employees.home"))
