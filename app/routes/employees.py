from datetime import timedelta

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import and_, asc, desc, func, or_
from sqlalchemy.orm import joinedload

from ..decorators import roles_required
from ..extensions import db
from ..models import (
    Attendance,
    Department,
    Employee,
    LeaveRequest,
    PayrollRecord,
    Role,
    User,
    create_audit_log,
    create_notification,
)
from ..utils import (
    decimal_value,
    get_current_employee,
    parse_date,
    parse_positive_int,
    store_today,
    validate_employee_form,
)

employee_bp = Blueprint("employees", __name__)


def _is_admin_experience():
    return current_user.is_authenticated and current_user.has_admin_access()


def _role_label(role: str) -> str:
    return Role.label(role)


@employee_bp.route("/")
def home():
    if current_user.is_authenticated:
        if current_user.has_admin_access():
            return redirect(url_for("employees.dashboard"))
        return redirect(url_for("employees.employee_dashboard"))
    return redirect(url_for("auth.login"))


@employee_bp.route("/management/overview")
@login_required
def operations_overview():
    if not current_user.has_admin_access():
        return redirect(url_for("employees.employee_dashboard"))

    today = store_today()
    recent_records = (
        PayrollRecord.query.options(joinedload(PayrollRecord.employee))
        .order_by(PayrollRecord.work_date.desc())
        .limit(8)
        .all()
    )

    trend_rows = (
        db.session.query(
            PayrollRecord.work_date, func.sum(PayrollRecord.total_pay).label("total_pay")
        )
        .filter(PayrollRecord.status.notin_(["Rejected", "Voided"]))
        .group_by(PayrollRecord.work_date)
        .order_by(PayrollRecord.work_date.desc())
        .limit(8)
        .all()
    )
    payroll_dates = [row.work_date.strftime("%b %d") for row in reversed(trend_rows)]
    payroll_totals = [float(row.total_pay) for row in reversed(trend_rows)]

    department_rows = (
        db.session.query(Department.name, func.count(Employee.id))
        .outerjoin(Employee)
        .group_by(Department.name)
        .order_by(Department.name)
        .all()
    )
    department_labels = [row[0] for row in department_rows]
    department_counts = [int(row[1]) for row in department_rows]

    pending_hours = PayrollRecord.query.filter_by(status="Pending").count()
    pending_leave_count = LeaveRequest.query.filter_by(status="Pending").count()
    active_clock_ins = Attendance.query.filter(Attendance.clock_out_at.is_(None)).count()
    total_employee_count = Employee.query.filter_by(archived=False).count()

    overtime_hours = (
        db.session.query(func.coalesce(func.sum(PayrollRecord.overtime_hours), 0.0))
        .filter(PayrollRecord.status.notin_(["Rejected", "Voided"]))
        .scalar()
    )
    regular_hours = (
        db.session.query(func.coalesce(func.sum(PayrollRecord.regular_hours), 0.0))
        .filter(PayrollRecord.status.notin_(["Rejected", "Voided"]))
        .scalar()
    )
    overtime_rate = round((overtime_hours / regular_hours) * 100, 1) if regular_hours else 0

    stats = {
        "employee_count": total_employee_count,
        "payroll_total_week": db.session.query(func.coalesce(func.sum(PayrollRecord.total_pay), 0))
        .filter(
            PayrollRecord.work_date.between(today - timedelta(days=today.weekday()), today),
            PayrollRecord.status.notin_(["Rejected", "Voided"]),
        )
        .scalar(),
        "overtime_rate": overtime_rate,
        "pending_approvals": pending_hours + pending_leave_count,
        "active_clock_ins": active_clock_ins,
    }

    alerts = []
    if pending_leave_count:
        alerts.append(f"{pending_leave_count} pending leave approvals")
    if pending_hours:
        alerts.append(f"{pending_hours} timesheets awaiting review")

    recent_hires = Employee.query.order_by(Employee.date_hired.desc()).limit(4).all()
    activity_logs = (
        db.session.query(
            User.username, PayrollRecord.work_date, PayrollRecord.status, PayrollRecord.total_pay
        )
        .join(Employee, User.employee_id == Employee.id)
        .join(PayrollRecord, PayrollRecord.employee_id == Employee.id)
        .order_by(PayrollRecord.created_at.desc())
        .limit(6)
        .all()
    )

    return render_template(
        "operations_overview.html",
        active_page="dashboard",
        page_title="Workforce Overview",
        today=today,
        stats=stats,
        recent_records=recent_records,
        payroll_dates=payroll_dates,
        payroll_totals=payroll_totals,
        department_labels=department_labels,
        department_counts=department_counts,
        attendance_labels=["Clocked In", "Off Shift"],
        attendance_values=[active_clock_ins, max(total_employee_count - active_clock_ins, 0)],
        alerts=alerts,
        recent_hires=recent_hires,
        activity_logs=activity_logs,
        shift_employee=get_current_employee(),
        active_shift=Attendance.query.filter_by(
            employee_id=current_user.employee_id, clock_out_at=None
        ).first(),
    )


@employee_bp.route("/dashboard")
@login_required
def dashboard():
    from .workplace import render_today

    if not current_user.has_admin_access():
        return redirect(url_for("employees.employee_dashboard"))
    return render_today()


@employee_bp.route("/employee/dashboard")
@login_required
def employee_dashboard():
    from .workplace import render_today

    if get_current_employee() is None:
        abort(403, description="Your account needs an employee profile. Contact an administrator.")
    return render_today()


@employee_bp.route("/employees")
@roles_required(*Role.ADMIN_LIKE)
def employee_directory():
    search = request.args.get("search", "").strip()
    department_id = (
        parse_positive_int(request.args.get("department_id"), default=None)
        if request.args.get("department_id")
        else None
    )
    role_filter = request.args.get("role", "") or None
    status_filter = request.args.get("status", "") or None
    page = parse_positive_int(request.args.get("page"), default=1)
    per_page = parse_positive_int(request.args.get("per_page"), default=20, maximum=100)
    sort = request.args.get("sort", "name")
    direction = request.args.get("direction", "asc")

    query = (
        Employee.query.options(joinedload(Employee.department), joinedload(Employee.user))
        .filter_by(archived=False)
        .outerjoin(User)
        .outerjoin(Department)
    )
    if search:
        search_term = f"%{search}%"
        query = query.filter(
            or_(
                Employee.first_name.ilike(search_term),
                Employee.last_name.ilike(search_term),
                Employee.email.ilike(search_term),
                Employee.job_title.ilike(search_term),
                Department.name.ilike(search_term),
            )
        )
    if role_filter in Role.CHOICES:
        query = query.filter(User.role == role_filter)
    if department_id:
        query = query.filter(Employee.department_id == department_id)

    today = store_today()
    probation_cutoff = today - timedelta(days=90)
    if status_filter == "on_leave":
        query = query.join(
            LeaveRequest,
            and_(
                LeaveRequest.employee_id == Employee.id,
                LeaveRequest.status == "Approved",
                LeaveRequest.start_date <= today,
                LeaveRequest.end_date >= today,
            ),
        ).distinct()
    elif status_filter == "probation":
        query = query.filter(Employee.date_hired >= probation_cutoff)
    elif status_filter == "active":
        query = query.filter(Employee.date_hired < probation_cutoff)

    sort_map = {
        "name": (Employee.last_name, Employee.first_name),
        "department": (Department.name, Employee.last_name),
        "email": (Employee.email, Employee.last_name),
        "rate": (Employee.hourly_rate, Employee.last_name),
    }
    sort_columns = sort_map.get(sort, sort_map["name"])
    order_by = [desc(column) if direction == "desc" else asc(column) for column in sort_columns]
    pagination = query.order_by(*order_by).paginate(page=page, per_page=per_page, error_out=False)

    departments = Department.query.order_by(Department.name).all()
    return render_template(
        "employees.html",
        active_page="employees",
        page_title="Workforce Directory",
        employees=pagination.items,
        pagination=pagination,
        departments=departments,
        search=search,
        selected_department=str(department_id) if department_id else "",
        selected_role=role_filter,
        selected_status=status_filter,
        sort=sort,
        direction=direction,
        per_page=per_page,
        today=today,
        role_choices=Role.CHOICES,
        role_label=_role_label,
        admin_view=_is_admin_experience(),
    )


@employee_bp.route("/team")
@login_required
def team_directory():
    search = request.args.get("search", "").strip()
    department_id = (
        parse_positive_int(request.args.get("department_id"), default=None)
        if request.args.get("department_id")
        else None
    )
    page = parse_positive_int(request.args.get("page"), default=1)
    per_page = parse_positive_int(request.args.get("per_page"), default=12, maximum=60)

    query = (
        Employee.query.options(joinedload(Employee.department))
        .filter_by(archived=False)
        .order_by(Employee.last_name, Employee.first_name)
    )
    if search:
        q = f"%{search}%"
        query = query.filter(
            or_(
                Employee.first_name.ilike(q),
                Employee.last_name.ilike(q),
                Employee.job_title.ilike(q),
            )
        )
    if department_id:
        query = query.filter(Employee.department_id == department_id)

    employees = query.paginate(page=page, per_page=per_page, error_out=False)
    departments = Department.query.order_by(Department.name).all()
    return render_template(
        "team.html",
        active_page="team",
        page_title="Team Directory",
        employees=employees.items,
        pagination=employees,
        departments=departments,
        selected_department=str(department_id) if department_id else "",
        search=search,
        per_page=per_page,
    )


@employee_bp.route("/employees/new", methods=("GET", "POST"))
@roles_required(*Role.PEOPLE_EDIT)
def add_employee():
    departments = Department.query.order_by(Department.name).all()
    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        job_title = request.form.get("job_title", "").strip()
        department_id = request.form.get("department_id", "") or None
        phone = request.form.get("phone", "").strip()
        address = request.form.get("address", "").strip()
        schedule = request.form.get("schedule", "").strip() or "Mon-Fri 9:00 AM - 5:00 PM"

        try:
            validate_employee_form(request.form)
            hourly_rate = decimal_value(request.form.get("hourly_rate", "0"))
            overtime_multiplier = decimal_value(
                request.form.get("overtime_multiplier", "1.5"), minimum=1, maximum=5
            )
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template(
                "employee_form.html",
                active_page="employees",
                title="Add Employee",
                employee=None,
                departments=departments,
            )

        if not first_name or not last_name or not email or not job_title:
            flash("Name, email, and job title are required.", "error")
        elif hourly_rate < 0 or overtime_multiplier < 0:
            flash("Pay values cannot be negative.", "error")
        elif Employee.query.filter_by(email=email).first():
            flash("An employee with that email already exists.", "error")
        else:
            employee = Employee(
                first_name=first_name,
                last_name=last_name,
                email=email,
                job_title=job_title,
                department_id=department_id,
                phone=phone,
                address=address,
                schedule=schedule,
                hourly_rate=hourly_rate,
                overtime_multiplier=overtime_multiplier,
                date_hired=store_today(),
            )
            db.session.add(employee)
            create_audit_log(
                current_user,
                "employee_created",
                "Employee",
                None,
                f"Employee {first_name} {last_name} was created.",
            )
            db.session.commit()
            flash("Employee added successfully.", "success")
            return redirect(url_for("employees.employee_directory"))

    return render_template(
        "employee_form.html",
        active_page="employees",
        title="Add Employee",
        employee=None,
        departments=departments,
    )


@employee_bp.route("/employees/<int:employee_id>")
@roles_required(*Role.ADMIN_LIKE)
def employee_detail(employee_id):
    employee = db.get_or_404(Employee, employee_id)
    return render_template(
        "employee_detail.html",
        employee=employee,
        active_page="employees",
        page_title=employee.full_name(),
    )


@employee_bp.route("/employees/<int:employee_id>/edit", methods=("GET", "POST"))
@roles_required(*Role.PEOPLE_EDIT)
def edit_employee(employee_id):
    employee = db.get_or_404(Employee, employee_id)
    departments = Department.query.order_by(Department.name).all()

    if request.method == "POST":
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        job_title = request.form.get("job_title", "").strip()
        department_id = request.form.get("department_id", "") or None
        phone = request.form.get("phone", "").strip()
        address = request.form.get("address", "").strip()
        schedule = request.form.get("schedule", "").strip() or "Mon-Fri 9:00 AM - 5:00 PM"

        try:
            validate_employee_form(request.form)
            hourly_rate = decimal_value(request.form.get("hourly_rate", "0"))
            overtime_multiplier = decimal_value(
                request.form.get("overtime_multiplier", "1.5"), minimum=1, maximum=5
            )
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template(
                "employee_form.html",
                active_page="employees",
                title="Edit Employee",
                employee=employee,
                departments=departments,
            )

        if not first_name or not last_name or not email or not job_title:
            flash("Name, email, and job title are required.", "error")
        elif hourly_rate < 0 or overtime_multiplier < 0:
            flash("Pay values cannot be negative.", "error")
        elif Employee.query.filter(Employee.email == email, Employee.id != employee.id).first():
            flash("An employee with that email already exists.", "error")
        else:
            employee.first_name = first_name
            employee.last_name = last_name
            employee.email = email
            employee.job_title = job_title
            employee.department_id = department_id
            employee.phone = phone
            employee.address = address
            employee.schedule = schedule
            employee.hourly_rate = hourly_rate
            employee.overtime_multiplier = overtime_multiplier
            create_audit_log(
                current_user,
                "employee_updated",
                "Employee",
                str(employee.id),
                f"Employee {employee.full_name()} profile updated.",
            )
            db.session.commit()
            flash("Employee updated successfully.", "success")
            return redirect(url_for("employees.employee_directory"))

    return render_template(
        "employee_form.html",
        active_page="employees",
        title="Edit Employee",
        employee=employee,
        departments=departments,
    )


@employee_bp.route("/employees/<int:employee_id>/delete", methods=("POST",))
@roles_required(Role.ADMIN, Role.EXECUTIVE)
def delete_employee(employee_id):
    employee = db.get_or_404(Employee, employee_id)
    if employee.id == current_user.employee_id:
        abort(400, description="You cannot archive your own employee record.")
    if Attendance.query.filter_by(employee_id=employee.id, clock_out_at=None).first():
        abort(400, description="Complete the active shift before archiving this employee.")
    create_audit_log(
        current_user,
        "employee_deleted",
        "Employee",
        str(employee.id),
        f"Employee {employee.full_name()} was archived.",
    )
    employee.archived = True
    db.session.commit()
    flash("Employee archived. Payroll, attendance, and audit history are preserved.", "success")
    return redirect(url_for("employees.employee_directory"))


@employee_bp.route("/profile", methods=("GET", "POST"))
@login_required
def profile():
    employee = get_current_employee()
    if request.method == "POST":
        if not employee:
            flash("Your account is not linked to an employee profile.", "error")
            return redirect(url_for("employees.profile"))

        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone = request.form.get("phone", "").strip()
        address = request.form.get("address", "").strip()

        try:
            validate_employee_form(request.form, compensation=False)
        except ValueError as exc:
            flash(str(exc), "error")
            return render_template("profile.html", active_page="profile", employee=employee)

        if not first_name or not last_name or not email:
            flash("Name and email are required.", "error")
        elif Employee.query.filter(Employee.email == email, Employee.id != employee.id).first():
            flash("That email address is already in use.", "error")
        else:
            employee.first_name = first_name
            employee.last_name = last_name
            employee.email = email
            employee.phone = phone
            employee.address = address
            create_audit_log(
                current_user,
                "profile_updated",
                "Employee",
                str(employee.id),
                f"{employee.full_name()} updated profile details.",
            )
            db.session.commit()
            flash("Profile updated successfully.", "success")
            return redirect(url_for("employees.profile"))

    return render_template(
        "profile.html", active_page="profile", page_title="My Profile", employee=employee
    )


@employee_bp.route("/payroll", methods=("GET", "POST"))
@login_required
def payroll():
    employees = []
    selected_employee_id = None
    admin_mode = current_user.has_admin_access()
    if admin_mode:
        employees = (
            Employee.query.filter_by(archived=False)
            .order_by(Employee.last_name, Employee.first_name)
            .all()
        )
        selected_employee_id = request.args.get("employee_id", "") or None
    else:
        selected_employee_id = current_user.employee_id
    page = parse_positive_int(request.args.get("page"), default=1)
    per_page = parse_positive_int(request.args.get("per_page"), default=20, maximum=100)

    if request.method == "POST":
        if admin_mode and current_user.role not in Role.PAYROLL_REVIEW:
            abort(403)
        if admin_mode:
            selected_employee_id = request.form.get("employee_id", "")
        if not selected_employee_id:
            flash("Please choose an employee.", "error")
            return redirect(url_for("employees.payroll"))

        value = str(selected_employee_id)
        selected_employee_id = (
            int(value)
            if value.isascii() and value.isdigit() and len(value) <= 18 and int(value) > 0
            else None
        )
        if not selected_employee_id:
            flash("Please choose a valid employee.", "error")
            return redirect(url_for("employees.payroll"))
        employee = db.get_or_404(Employee, selected_employee_id)
        work_date = parse_date(request.form.get("work_date", ""))
        try:
            hours_worked = decimal_value(request.form.get("hours_worked", "0"), maximum=24)
        except ValueError:
            work_date = None
            hours_worked = None

        if not work_date or work_date > store_today() or hours_worked is None:
            flash("Provide a valid work date (today or earlier) and 0–24 daily hours.", "error")
            return redirect(
                url_for(
                    "employees.payroll", employee_id=selected_employee_id if admin_mode else None
                )
            )

        notes = request.form.get("notes", "").strip()
        if employee.archived or len(notes) > 2000:
            flash("Choose an active employee and notes of at most 2000 characters.", "error")
            return redirect(
                url_for(
                    "employees.payroll", employee_id=selected_employee_id if admin_mode else None
                )
            )

        record = PayrollRecord.query.filter_by(employee_id=employee.id, work_date=work_date).first()
        previous_hours = str(record.hours_worked) if record else "new"
        if record and (record.legacy_import or (record.status == "Approved" and not admin_mode)):
            abort(
                403,
                description="Approved entries require an authorized correction; imported records require reconciliation.",
            )
        if record:
            record.hours_worked = hours_worked
            record.attendance_fingerprint = None
            record.notes = notes
            record.status = "Pending"
            record.reviewed_by_id = None
            record.reviewed_at = None
        else:
            record = PayrollRecord.build(employee, work_date, hours_worked, notes)
            record.status = "Pending"
            db.session.add(record)

        try:
            db.session.flush()
            PayrollRecord.recalculate_week(employee.id, work_date)
        except ValueError as exc:
            db.session.rollback()
            flash(str(exc), "error")
            return redirect(url_for("employees.payroll"))

        create_audit_log(
            current_user,
            "payroll_created",
            "PayrollRecord",
            str(record.id),
            f"Payroll entry for {employee.full_name()} on {work_date}: hours {previous_hours} → {hours_worked}; weekly allocation recalculated. Source: manual hours; any prior clock-source link cleared.",
        )
        if admin_mode:
            if employee.user:
                create_notification(
                    employee.user,
                    "Hours updated",
                    f"Your hours entry for {work_date} is now {record.status.lower()}. Its workweek was recalculated; changed entries require review.",
                    url="/payroll",
                    category="info",
                )
            db.session.commit()
            flash("Payroll record saved successfully.", "success")
        else:
            admin_users = User.query.filter(User.role.in_(Role.PAYROLL_REVIEW)).all()
            for user in admin_users:
                create_notification(
                    user,
                    "Timesheet pending review",
                    f"{employee.full_name()} submitted {hours_worked:.2f} hours for {work_date}.",
                    "/payroll?employee_id=" + str(employee.id),
                    "approval",
                )
            db.session.commit()
            flash("Hours logged successfully. Awaiting approval.", "success")

        return redirect(
            url_for("employees.payroll", employee_id=employee.id if admin_mode else None)
        )

    records_query = PayrollRecord.query.options(joinedload(PayrollRecord.employee))
    records_query = records_query.filter(PayrollRecord.status != "Voided")
    if admin_mode and selected_employee_id:
        value = str(selected_employee_id)
        if not value.isascii() or not value.isdigit() or len(value) > 18 or int(value) < 1:
            abort(400, description="Choose a valid employee filter.")
        selected_employee_id = int(value)
        db.get_or_404(Employee, selected_employee_id)
        records_query = records_query.filter_by(employee_id=selected_employee_id)
    if not admin_mode:
        records_query = records_query.filter_by(employee_id=current_user.employee_id)
    totals = (
        records_query.filter(PayrollRecord.status != "Rejected")
        .with_entities(
            func.coalesce(func.sum(PayrollRecord.hours_worked), 0.0),
            func.coalesce(func.sum(PayrollRecord.regular_hours), 0.0),
            func.coalesce(func.sum(PayrollRecord.overtime_hours), 0.0),
            func.coalesce(func.sum(PayrollRecord.total_pay), 0.0),
        )
        .first()
    )
    pagination = records_query.order_by(PayrollRecord.work_date.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    records = pagination.items
    from ..time_review import source_stale_ids
    from .workplace import workplace_zone

    summary = {
        "hours": float(totals[0]),
        "regular_hours": float(totals[1]),
        "overtime_hours": float(totals[2]),
        "total_pay": float(totals[3]),
    }

    return render_template(
        "payroll.html",
        active_page="payroll",
        page_title="Hours and Payroll",
        records=records,
        summary=summary,
        employees=employees,
        selected_employee=selected_employee_id,
        pagination=pagination,
        per_page=per_page,
        admin_view=admin_mode,
        store_today=store_today().isoformat(),
        punch_stale=source_stale_ids(records, workplace_zone()),
    )


@employee_bp.route("/payroll/<int:record_id>/review", methods=("POST",))
@roles_required(*Role.PAYROLL_REVIEW)
def review_payroll(record_id):
    from ..time_review import serialize_command

    serialize_command()
    record = db.get_or_404(PayrollRecord, record_id)
    if record.employee_id == current_user.employee_id or record.legacy_import:
        abort(403, description="You cannot review your own or an imported timesheet.")
    if record.status not in {"Pending", "Reviewed"}:
        abort(409, description="This timesheet is no longer awaiting review. Refresh the page.")
    if request.form.get("version_id") != str(record.version_id):
        abort(409, description="This timesheet changed. Refresh it before reviewing.")
    action = request.form.get("action", "review")
    if action not in {"review", "approve", "reject"}:
        flash("Invalid review action.", "error")
        return redirect(url_for("employees.payroll", employee_id=record.employee_id))

    status_map = {"review": "Reviewed", "approve": "Approved", "reject": "Rejected"}
    if action == "approve":
        from ..payroll_review import require_payroll_approval

        require_payroll_approval(record)
    record.status = status_map[action]
    record.reviewed_by_id = current_user.id
    record.reviewed_at = db.func.now()
    if action == "reject":
        try:
            db.session.flush()
            PayrollRecord.recalculate_week(record.employee_id, record.work_date)
        except ValueError as exc:
            db.session.rollback()
            abort(400, description=str(exc))

    if record.employee.user:
        create_notification(
            record.employee.user,
            "Timesheet updated",
            f"Your {record.work_date} entry is now {record.status.lower()}.",
            "/payroll",
            "info" if action != "reject" else "warning",
        )

    create_audit_log(
        current_user,
        "payroll_reviewed",
        "PayrollRecord",
        str(record.id),
        f"Payroll entry for {record.employee.full_name()} marked {record.status.lower()}.",
    )
    db.session.commit()
    flash(f"Entry marked as {record.status.lower()}.", "success")
    return redirect(url_for("employees.payroll", employee_id=record.employee_id))


@employee_bp.route("/payroll/<int:record_id>/delete", methods=("POST",))
@roles_required(*Role.PAYROLL_REVIEW)
def delete_payroll(record_id):
    record = db.get_or_404(PayrollRecord, record_id)
    if record.employee_id == current_user.employee_id:
        abort(403, description="You cannot void your own timesheet.")
    if record.legacy_import:
        abort(400, description="Imported records require manual reconciliation before changes.")
    if record.status == "Voided":
        abort(409, description="This timesheet has already been voided.")
    if request.form.get("version_id") != str(record.version_id):
        abort(409, description="This timesheet changed. Refresh it before voiding.")
    employee_id = record.employee_id
    create_audit_log(
        current_user,
        "payroll_deleted",
        "PayrollRecord",
        str(record.id),
        f"Payroll entry removed for {record.employee.full_name()}.",
    )
    record.status = "Voided"
    record.reviewed_by_id = current_user.id
    record.reviewed_at = db.func.now()
    try:
        db.session.flush()
        PayrollRecord.recalculate_week(employee_id, record.work_date)
    except ValueError as exc:
        db.session.rollback()
        abort(400, description=str(exc))
    db.session.commit()
    flash(
        "Payroll entry voided. Historical record retained and weekly totals recalculated.",
        "success",
    )
    return redirect(url_for("employees.payroll", employee_id=employee_id))


@employee_bp.route("/settings")
@roles_required(*Role.ADMIN_LIKE)
def settings():
    return render_template(
        "settings.html",
        active_page="settings",
        page_title="System Settings",
        policy=current_app.config,
    )
