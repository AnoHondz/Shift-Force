from datetime import date, timedelta

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from .auth import executive_required, login_required
from .db import get_db


bp = Blueprint("employees", __name__)


def calculate_pay(hours_worked, hourly_rate, overtime_rate):
    regular_hours = min(hours_worked, 8)
    overtime_hours = max(hours_worked - 8, 0)
    total_pay = (regular_hours * hourly_rate) + (overtime_hours * hourly_rate * overtime_rate)
    return regular_hours, overtime_hours, total_pay


def fetch_departments():
    db = get_db()
    return db.execute(
        "SELECT dept_id AS id, dept_name AS name FROM departments ORDER BY dept_name"
    ).fetchall()


def fetch_employee(employee_id):
    db = get_db()
    return db.execute(
        """
        SELECT
            e.id,
            e.first_name,
            e.last_name,
            e.email,
            e.job_title,
            e.salary,
            e.overtime_rate,
            e.schedule,
            e.address,
            e.phone,
            e.dept_id,
            d.dept_name AS department_name
        FROM employees e
        LEFT JOIN departments d ON d.dept_id = e.dept_id
        WHERE e.id = ?
        """,
        (employee_id,),
    ).fetchone()


def fetch_employees(search="", department_id=None, limit=None):
    db = get_db()
    query = """
        SELECT
            e.id,
            e.first_name,
            e.last_name,
            e.email,
            e.job_title,
            e.salary,
            e.overtime_rate,
            e.schedule,
            e.dept_id,
            d.dept_name AS department_name
        FROM employees e
        LEFT JOIN departments d ON d.dept_id = e.dept_id
        WHERE 1 = 1
    """
    params = []

    if search:
        query += """
            AND (
                e.first_name LIKE ?
                OR e.last_name LIKE ?
                OR e.job_title LIKE ?
                OR e.email LIKE ?
                OR COALESCE(d.dept_name, '') LIKE ?
            )
        """
        search_term = f"%{search}%"
        params.extend([search_term] * 5)

    if department_id:
        query += " AND e.dept_id = ?"
        params.append(department_id)

    query += " ORDER BY e.last_name, e.first_name"
    if limit:
        query += " LIMIT ?"
        params.append(limit)

    return db.execute(query, params).fetchall()


def fetch_hours_logs(employee_id=None, limit=None):
    db = get_db()
    query = """
        SELECT
            h.id,
            h.employee_id,
            h.work_date,
            h.hours_worked,
            h.notes,
            e.first_name,
            e.last_name,
            e.job_title,
            e.salary,
            e.overtime_rate,
            d.dept_name AS department_name
        FROM hours_logs h
        JOIN employees e ON e.id = h.employee_id
        LEFT JOIN departments d ON d.dept_id = e.dept_id
        WHERE 1 = 1
    """
    params = []

    if employee_id:
        query += " AND h.employee_id = ?"
        params.append(employee_id)

    query += " ORDER BY h.work_date DESC, h.id DESC"
    if limit:
        query += " LIMIT ?"
        params.append(limit)

    rows = db.execute(query, params).fetchall()
    decorated = []
    for row in rows:
        regular_hours, overtime_hours, total_pay = calculate_pay(
            float(row["hours_worked"]),
            float(row["salary"]),
            float(row["overtime_rate"]),
        )
        decorated.append(
            {
                **dict(row),
                "regular_hours": regular_hours,
                "overtime_hours": overtime_hours,
                "total_pay": total_pay,
            }
        )
    return decorated


def summarize_hours(rows):
    return {
        "hours": sum(row["hours_worked"] for row in rows),
        "regular_hours": sum(row["regular_hours"] for row in rows),
        "overtime_hours": sum(row["overtime_hours"] for row in rows),
        "total_pay": sum(row["total_pay"] for row in rows),
    }


@bp.route("/")
def home():
    if g.user:
        return redirect(url_for("employees.dashboard"))
    return render_template("landing.html", active_page="home")


@bp.route("/dashboard")
@login_required
def dashboard():
    db = get_db()
    employees = fetch_employees(limit=8)

    if g.user["role"] == "executive":
        week_start = date.today() - timedelta(days=date.today().weekday())
        week_logs = fetch_hours_logs()
        week_total_hours = sum(log["hours_worked"] for log in week_logs if log["work_date"] >= week_start.isoformat())
        week_total_pay = sum(log["total_pay"] for log in week_logs if log["work_date"] >= week_start.isoformat())
        stats = {
            "employee_count": db.execute("SELECT COUNT(*) AS count FROM employees").fetchone()["count"],
            "average_rate": db.execute("SELECT COALESCE(AVG(salary), 0) AS average_rate FROM employees").fetchone()["average_rate"],
            "week_total_hours": week_total_hours,
            "week_total_pay": week_total_pay,
        }
        recent_logs = fetch_hours_logs(limit=6)
        return render_template(
            "dashboard.html",
            active_page="dashboard",
            stats=stats,
            employees=employees,
            recent_logs=recent_logs,
            my_profile=None,
            my_summary=None,
        )

    my_profile = fetch_employee(g.user["employee_id"]) if g.user["employee_id"] else None
    my_logs = fetch_hours_logs(g.user["employee_id"], limit=8) if g.user["employee_id"] else []
    my_summary = summarize_hours(my_logs) if my_logs else {"hours": 0, "regular_hours": 0, "overtime_hours": 0, "total_pay": 0}
    return render_template(
        "dashboard.html",
        active_page="dashboard",
        stats=None,
        employees=employees,
        recent_logs=my_logs,
        my_profile=my_profile,
        my_summary=my_summary,
    )


@bp.route("/employees")
@login_required
def employee_directory():
    search = request.args.get("search", "").strip()
    department_id = request.args.get("department_id", "").strip() or None
    departments = fetch_departments()
    employees = fetch_employees(search=search, department_id=department_id)
    return render_template(
        "employees.html",
        active_page="employees",
        employees=employees,
        departments=departments,
        search=search,
        selected_department=department_id,
    )


@bp.route("/employees/new", methods=("GET", "POST"))
@executive_required
def add_employee():
    db = get_db()
    departments = fetch_departments()

    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        job_title = request.form.get("job_title", "").strip()
        department_id = request.form.get("department_id", "").strip() or None
        schedule = request.form.get("schedule", "").strip() or "Mon-Fri 9:00 AM - 5:00 PM"
        address = request.form.get("address", "").strip()
        phone = request.form.get("phone", "").strip()
        error = None

        try:
            hourly_rate = float(request.form.get("hourly_rate", "0"))
            overtime_rate = float(request.form.get("overtime_rate", "1.5"))
        except ValueError:
            error = "Hourly pay and overtime rate must be numbers."
            hourly_rate = 0
            overtime_rate = 0

        if not first_name or not last_name or not email or not job_title:
            error = "First name, last name, email, and position are required."
        elif hourly_rate < 0 or overtime_rate < 0:
            error = "Pay rates cannot be negative."
        elif db.execute("SELECT 1 FROM employees WHERE email = ?", (email,)).fetchone():
            error = "An employee with that email already exists."

        if error is None and department_id:
            if db.execute("SELECT 1 FROM departments WHERE dept_id = ?", (department_id,)).fetchone() is None:
                error = "Selected department does not exist."

        if error is None:
            next_id = db.execute("SELECT COALESCE(MAX(id), 0) + 1 AS next_id FROM employees").fetchone()["next_id"]
            db.execute(
                """
                INSERT INTO employees (
                    id, first_name, last_name, email, job_title, salary, dept_id,
                    overtime_rate, schedule, address, phone
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    next_id,
                    first_name,
                    last_name,
                    email,
                    job_title,
                    hourly_rate,
                    department_id,
                    overtime_rate,
                    schedule,
                    address,
                    phone,
                ),
            )
            db.commit()
            flash("Employee added successfully.", "success")
            return redirect(url_for("employees.employee_directory"))

        flash(error, "error")

    return render_template(
        "employee_form.html",
        active_page="add-employee",
        title="Add Employee",
        employee=None,
        departments=departments,
    )


@bp.route("/employees/<int:employee_id>/edit", methods=("GET", "POST"))
@executive_required
def edit_employee(employee_id):
    db = get_db()
    employee = fetch_employee(employee_id)
    departments = fetch_departments()

    if employee is None:
        flash("Employee not found.", "error")
        return redirect(url_for("employees.employee_directory"))

    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        job_title = request.form.get("job_title", "").strip()
        department_id = request.form.get("department_id", "").strip() or None
        schedule = request.form.get("schedule", "").strip() or "Mon-Fri 9:00 AM - 5:00 PM"
        address = request.form.get("address", "").strip()
        phone = request.form.get("phone", "").strip()
        error = None

        try:
            hourly_rate = float(request.form.get("hourly_rate", "0"))
            overtime_rate = float(request.form.get("overtime_rate", "1.5"))
        except ValueError:
            error = "Hourly pay and overtime rate must be numbers."
            hourly_rate = 0
            overtime_rate = 0

        if not first_name or not last_name or not email or not job_title:
            error = "First name, last name, email, and position are required."
        elif hourly_rate < 0 or overtime_rate < 0:
            error = "Pay rates cannot be negative."
        elif db.execute("SELECT 1 FROM employees WHERE email = ? AND id <> ?", (email, employee_id)).fetchone():
            error = "An employee with that email already exists."

        if error is None and department_id:
            if db.execute("SELECT 1 FROM departments WHERE dept_id = ?", (department_id,)).fetchone() is None:
                error = "Selected department does not exist."

        if error is None:
            if float(employee["salary"]) != hourly_rate:
                db.execute(
                    """
                    INSERT INTO salary_history (emp_id, old_salary, new_salary, changed_by)
                    VALUES (?, ?, ?, ?)
                    """,
                    (employee_id, employee["salary"], hourly_rate, g.user["id"]),
                )

            db.execute(
                """
                UPDATE employees
                SET first_name = ?, last_name = ?, email = ?, job_title = ?, salary = ?,
                    dept_id = ?, overtime_rate = ?, schedule = ?, address = ?, phone = ?
                WHERE id = ?
                """,
                (
                    first_name,
                    last_name,
                    email,
                    job_title,
                    hourly_rate,
                    department_id,
                    overtime_rate,
                    schedule,
                    address,
                    phone,
                    employee_id,
                ),
            )
            db.commit()
            flash("Employee updated successfully.", "success")
            return redirect(url_for("employees.employee_directory"))

        flash(error, "error")

    return render_template(
        "employee_form.html",
        active_page="employees",
        title="Edit Employee",
        employee=employee,
        departments=departments,
    )


@bp.route("/profile", methods=("GET", "POST"))
@login_required
def profile():
    db = get_db()
    employee = fetch_employee(g.user["employee_id"]) if g.user["employee_id"] else None

    if request.method == "POST":
        if not employee:
            flash("No employee profile is linked to this account.", "error")
            return redirect(url_for("employees.profile"))

        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        address = request.form.get("address", "").strip()
        phone = request.form.get("phone", "").strip()
        schedule = request.form.get("schedule", "").strip()

        if not first_name or not last_name or not email:
            flash("First name, last name, and email are required.", "error")
        elif db.execute("SELECT 1 FROM employees WHERE email = ? AND id <> ?", (email, employee["id"])).fetchone():
            flash("Another employee is already using that email address.", "error")
        else:
            db.execute(
                """
                UPDATE employees
                SET first_name = ?, last_name = ?, email = ?, address = ?, phone = ?, schedule = ?
                WHERE id = ?
                """,
                (first_name, last_name, email, address, phone, schedule, employee["id"]),
            )
            db.commit()
            flash("Profile updated successfully.", "success")
            return redirect(url_for("employees.profile"))

    return render_template("profile.html", active_page="profile", employee=employee)


@bp.route("/hours", methods=("GET", "POST"))
@login_required
def hours():
    db = get_db()
    if request.method == "POST":
        if g.user["role"] == "executive":
            selected_employee_id = request.form.get("employee_id", "").strip()
            if not selected_employee_id:
                flash("Executives must select an employee.", "error")
                return redirect(url_for("employees.hours"))
            employee_record = fetch_employee(selected_employee_id)
            if employee_record is None:
                flash("Selected employee does not exist.", "error")
                return redirect(url_for("employees.hours"))
            target_employee_id = selected_employee_id
        elif g.user["employee_id"]:
            target_employee_id = g.user["employee_id"]
        else:
            flash("No employee profile is linked to this account.", "error")
            return redirect(url_for("employees.hours"))

        work_date = request.form.get("work_date", "").strip()
        notes = request.form.get("notes", "").strip()

        try:
            hours_worked = float(request.form.get("hours_worked", "0"))
        except ValueError:
            hours_worked = -1

        if not work_date:
            flash("Work date is required.", "error")
        elif hours_worked < 0 or hours_worked > 24:
            flash("Hours worked must be between 0 and 24.", "error")
        else:
            # Upsert keeps one time entry per employee per day.
            db.execute(
                """
                INSERT INTO hours_logs (employee_id, work_date, hours_worked, notes)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(employee_id, work_date)
                DO UPDATE SET hours_worked = excluded.hours_worked, notes = excluded.notes
                """,
                (target_employee_id, work_date, hours_worked, notes),
            )
            db.commit()
            flash("Hours saved successfully.", "success")
            if g.user["role"] == "executive":
                return redirect(url_for("employees.hours", employee_id=target_employee_id))
            return redirect(url_for("employees.hours"))

    selected_employee = request.args.get("employee_id", "").strip() if g.user["role"] == "executive" else None
    employee_id = selected_employee or g.user["employee_id"]
    logs = fetch_hours_logs(employee_id)
    summary = summarize_hours(logs)
    employees = fetch_employees() if g.user["role"] == "executive" else []
    return render_template(
        "hours.html",
        active_page="hours",
        logs=logs,
        summary=summary,
        employees=employees,
        selected_employee=selected_employee,
        employee_profile=fetch_employee(g.user["employee_id"]) if g.user["employee_id"] else None,
    )


@bp.route("/hours/<int:log_id>/delete", methods=("POST",))
@executive_required
def delete_hours_log(log_id):
    db = get_db()
    log = db.execute("SELECT employee_id FROM hours_logs WHERE id = ?", (log_id,)).fetchone()
    if log is None:
        flash("Hours log not found.", "error")
        return redirect(url_for("employees.hours"))

    db.execute("DELETE FROM hours_logs WHERE id = ?", (log_id,))
    db.commit()
    flash("Hours log deleted successfully.", "success")
    return redirect(url_for("employees.hours", employee_id=log["employee_id"]))
