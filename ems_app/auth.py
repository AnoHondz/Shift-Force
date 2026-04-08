from functools import wraps

from flask import (
    Blueprint,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db


bp = Blueprint("auth", __name__)


def get_next_url(default_endpoint="employees.dashboard"):
    next_url = request.args.get("next") or request.form.get("next")
    if next_url and next_url.startswith("/"):
        return next_url
    return url_for(default_endpoint)


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("auth.login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)

    return wrapped_view


def executive_required(view):
    @wraps(view)
    @login_required
    def wrapped_view(*args, **kwargs):
        if g.user["role"] != "executive":
            flash("Executive access is required for that page.", "error")
            return redirect(url_for("employees.dashboard"))
        return view(*args, **kwargs)

    return wrapped_view


@bp.before_app_request
def load_logged_in_user():
    user_id = session.get("user_id")
    if user_id is None:
        g.user = None
        return

    db = get_db()
    g.user = db.execute(
        """
        SELECT
            u.id,
            u.username,
            u.role,
            u.employee_id,
            e.first_name,
            e.last_name,
            e.email,
            e.job_title,
            e.salary,
            e.overtime_rate,
            e.schedule,
            e.address,
            e.phone,
            d.dept_name AS department_name
        FROM users u
        LEFT JOIN employees e ON e.id = u.employee_id
        LEFT JOIN departments d ON d.dept_id = e.dept_id
        WHERE u.id = ?
        """,
        (user_id,),
    ).fetchone()


@bp.route("/signup", methods=("GET", "POST"))
def signup():
    db = get_db()
    departments = db.execute(
        "SELECT dept_id AS id, dept_name AS name FROM departments ORDER BY dept_name"
    ).fetchall()

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "employee").strip()
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        job_title = request.form.get("job_title", "").strip() or "Employee"
        department_id = request.form.get("department_id", "").strip() or None
        invite_code = request.form.get("invite_code", "").strip()
        error = None

        if not username:
            error = "Username is required."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif role not in {"executive", "employee"}:
            error = "Please choose a valid role."
        elif role == "employee" and (not first_name or not last_name or not email):
            error = "Employees must provide first name, last name, and email."
        elif role == "executive" and invite_code != current_app.config["EXECUTIVE_SIGNUP_CODE"]:
            error = "Executive signup code is invalid."
        elif db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone():
            error = "That username is already taken."

        linked_employee_id = None
        if error is None and role == "employee":
            if department_id:
                department = db.execute(
                    "SELECT dept_id FROM departments WHERE dept_id = ?",
                    (department_id,),
                ).fetchone()
                if department is None:
                    error = "Selected department does not exist."

            existing_employee = db.execute(
                "SELECT id FROM employees WHERE lower(email) = lower(?)",
                (email,),
            ).fetchone()

            if existing_employee:
                linked_employee_id = existing_employee["id"]
                linked_user = db.execute(
                    "SELECT 1 FROM users WHERE employee_id = ?",
                    (linked_employee_id,),
                ).fetchone()
                if linked_user:
                    error = "That employee profile is already linked to a user account."
                else:
                    db.execute(
                        """
                        UPDATE employees
                        SET first_name = ?, last_name = ?, job_title = COALESCE(NULLIF(job_title, ''), ?), dept_id = COALESCE(dept_id, ?)
                        WHERE id = ?
                        """,
                        (first_name, last_name, job_title, department_id, linked_employee_id),
                    )
            else:
                next_employee_id = db.execute("SELECT COALESCE(MAX(id), 0) + 1 AS next_id FROM employees").fetchone()["next_id"]
                db.execute(
                    """
                    INSERT INTO employees (
                        id, first_name, last_name, email, job_title, salary, dept_id, overtime_rate, schedule, address, phone
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', '')
                    """,
                    (
                        next_employee_id,
                        first_name,
                        last_name,
                        email,
                        job_title,
                        25.0,
                        department_id,
                        1.5,
                        "Mon-Fri 9:00 AM - 5:00 PM",
                    ),
                )
                linked_employee_id = next_employee_id

        if error is None:
            db.execute(
                """
                INSERT INTO users (username, password_hash, role, employee_id)
                VALUES (?, ?, ?, ?)
                """,
                (username, generate_password_hash(password), role, linked_employee_id),
            )
            db.commit()
            flash("Account created successfully. Please log in.", "success")
            return redirect(url_for("auth.login"))

        flash(error, "error")

    return render_template("signup.html", departments=departments, active_page="signup")


@bp.route("/login", methods=("GET", "POST"))
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        db = get_db()
        user = db.execute(
            "SELECT id, username, password_hash FROM users WHERE username = ?",
            (username,),
        ).fetchone()

        if user is None or not check_password_hash(user["password_hash"], password):
            flash("Invalid username or password.", "error")
        else:
            session.clear()
            session["user_id"] = user["id"]
            flash("Welcome back.", "success")
            return redirect(get_next_url())

    return render_template("login.html", next_url=get_next_url(), active_page="login")


@bp.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "success")
    return redirect(url_for("employees.home"))
