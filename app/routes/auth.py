from datetime import date
from urllib.parse import unquote, urlsplit

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import or_

from ..extensions import db, limiter, login_manager
from ..models import Employee, Role, User, create_audit_log
from ..stores import invited_employee
from ..utils import valid_email, valid_password

auth_bp = Blueprint("auth", __name__)


@login_manager.user_loader
def load_user(user_id):
    if not user_id.isdigit():
        return None
    user = db.session.get(User, int(user_id))
    return user if user and user.is_active else None


def get_dashboard_endpoint(user):
    if user and user.role == Role.OWNER:
        return "stores.index"
    if user and user.has_admin_access():
        return "employees.dashboard"
    return "employees.employee_dashboard"


def get_next_url(default_endpoint="employees.dashboard"):
    next_url = request.args.get("next") or request.form.get("next")
    parsed = urlsplit(unquote(next_url or ""))
    if (
        next_url
        and next_url.startswith("/")
        and not parsed.netloc
        and not parsed.scheme
        and not unquote(next_url).startswith("//")
        and "\\" not in unquote(next_url)
        and not any(ord(c) < 32 for c in unquote(next_url))
        and not any(segment in {".", ".."} for segment in parsed.path.split("/"))
    ):
        if request.script_root:
            if unquote(next_url).startswith(request.script_root + "/"):
                return next_url
            hub_prefix = request.environ.get("shiftspace.hub_prefix", "")
            if unquote(next_url).startswith(hub_prefix + "/s/"):
                return url_for(default_endpoint)
            return request.script_root + next_url
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
        "invited_employee": None,
        "owner_signup": False,
    }
    context["invite_token"] = None
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
@auth_bp.route("/owner/signup", methods=("GET", "POST"), endpoint="signup_owner")
@limiter.limit("10 per hour", methods=["POST"])
def signup():
    owner_signup = request.endpoint == "auth.signup_owner"
    if owner_signup and (
        current_app.config.get("STORE_SLUG")
        or not current_app.config.get(
            "PUBLIC_STORE_OWNER_REGISTRATION", current_app.config.get("PUBLIC_REGISTRATION", True)
        )
    ):
        abort(403, description="Contact the platform administrator to create an owner account.")
    invitation = request.args.get("invite") or request.form.get("invite")
    invited = None
    if not owner_signup and not current_app.config.get("PUBLIC_REGISTRATION", True):
        invited = invited_employee(invitation)
        if not invited:
            abort(
                403, description="Use an invitation from your store manager to create an account."
            )
    if current_user.is_authenticated:
        return redirect(url_for(get_dashboard_endpoint(current_user)))
    if request.method == "POST":
        full_name = invited.full_name() if invited else request.form.get("full_name", "").strip()
        username = request.form.get("username", "").strip()
        email = invited.email if invited else request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")

        signup_values = {
            "full_name": full_name,
            "username": username,
            "email": email,
        }
        signup_errors = {}

        first_name, last_name = _split_full_name(full_name)
        if not first_name or not last_name or len(first_name) > 120 or len(last_name) > 120:
            signup_errors["full_name"] = "Enter your full name (first and last)."
        if not 3 <= len(username) <= 120 or not all(c.isalnum() or c in "._-" for c in username):
            signup_errors["username"] = "Use 3–120 letters, numbers, dots, dashes, or underscores."
        elif User.query.filter_by(username=username).first():
            signup_errors["username"] = "That username is already taken."
        if not valid_email(email):
            signup_errors["email"] = "Enter a valid email address."
        elif not invited and Employee.query.filter_by(email=email).first():
            signup_errors["email"] = (
                "That email is already associated with an employee record. Contact an administrator."
            )
        if not valid_password(password):
            # B105 mistakes the validation message for a hardcoded password.
            signup_errors["password"] = "Password must be 12–128 characters."  # nosec B105
        if password != confirm_password:
            # B105 mistakes the validation message for a hardcoded password.
            signup_errors["confirm_password"] = "Passwords do not match."  # nosec B105

        if not signup_errors:
            employee = invited or Employee(
                first_name=first_name,
                last_name=last_name,
                email=email,
                job_title="Store owner" if owner_signup else "Employee",
                department_id=None,
                hourly_rate=0 if owner_signup else 25.0,
                overtime_multiplier=1.5,
                date_hired=date.today(),
            )
            db.session.add(employee)
            db.session.flush()

            user = User(
                username=username,
                role=Role.OWNER if owner_signup else Role.EMPLOYEE,
                employee_id=employee.id,
            )
            user.set_password(password)
            db.session.add(user)
            if invited:
                create_audit_log(
                    None,
                    "account_created",
                    "Employee",
                    str(employee.id),
                    "Employee accepted a store invitation.",
                )
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
                invite_token=invitation,
                invited_employee=invited,
                owner_signup=owner_signup,
            ),
        )

    return render_template(
        "login.html",
        **_auth_template_context(
            active_tab="signup",
            invite_token=invitation,
            invited_employee=invited,
            owner_signup=owner_signup,
            signup_values={"full_name": invited.full_name(), "email": invited.email}
            if invited
            else {},
        ),
    )


@auth_bp.route("/login", methods=("GET", "POST"))
@limiter.limit("10 per minute", methods=["POST"])
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

        if (
            user is None
            or not user.is_active
            or len(password) > 128
            or not user.check_password(password)
        ):
            login_error = "Invalid username or password."
            flash(login_error, "error")
            create_audit_log(
                None,
                "login_failed",
                "User",
                None,
                "Failed login attempt.",
            )
            db.session.commit()
        else:
            session.clear()
            session.permanent = remember_me
            login_user(user, remember=remember_me)
            create_audit_log(
                user, "login_success", "User", str(user.id), f"{user.username} logged in."
            )
            db.session.commit()
            flash("Welcome back.", "success")
            return redirect(get_next_url(get_dashboard_endpoint(user)))

    return render_template(
        "login.html",
        **_auth_template_context(login_error=login_error, active_tab="signin"),
    )


@auth_bp.route("/logout", methods=("POST",))
@login_required
def logout():
    create_audit_log(
        current_user, "logout", "User", str(current_user.id), f"{current_user.username} logged out."
    )
    db.session.commit()
    session.clear()
    # Keep Flask-Login's remember-cookie deletion flag until after_request runs.
    logout_user()
    flash("You have been logged out.", "success")
    return redirect(url_for("auth.login"))
