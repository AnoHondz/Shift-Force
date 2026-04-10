from datetime import date

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_, func
from sqlalchemy.orm import joinedload

from ..decorators import roles_required
from ..extensions import db
from ..models import Attendance, Department, Employee, LeaveRequest, PayrollRecord, Role, User
from ..utils import get_current_employee, parse_date


employee_bp = Blueprint("employees", __name__)


@employee_bp.route("/")
def home():
    if current_user.is_authenticated:
        return redirect(url_for("employees.dashboard"))
    return render_template("landing.html", active_page="home")


@employee_bp.route("/dashboard")
@login_required
def dashboard():
    employees = (
        Employee.query.options(joinedload(Employee.department))
        .order_by(Employee.last_name, Employee.first_name)
        .limit(8)
        .all()
    )
    if current_user.is_admin() or current_user.is_manager():
        payroll_records = PayrollRecord.query.order_by(PayrollRecord.work_date.desc()).limit(6).all()
        trend_rows = (
            db.session.query(
                PayrollRecord.work_date,
                func.sum(PayrollRecord.total_pay).label("total_pay"),
            )
            .group_by(PayrollRecord.work_date)
            .order_by(PayrollRecord.work_date.desc())
            .limit(8)
            .all()
        )
        payroll_dates = [row.work_date.isoformat() for row in reversed(trend_rows)]
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

        unassigned_count = Employee.query.filter(Employee.department_id.is_(None)).count()
        if unassigned_count:
            department_labels.append("Unassigned")
            department_counts.append(unassigned_count)

        active_clock_ins = Attendance.query.filter(Attendance.clock_out_at.is_(None)).count()
        total_employee_count = Employee.query.count()
        attendance_labels = ["Clocked In", "Not Clocked In"]
        attendance_values = [active_clock_ins, max(total_employee_count - active_clock_ins, 0)]

        pending_leave_count = LeaveRequest.query.filter_by(status="Pending").count()

        stats = {
            "employee_count": total_employee_count,
            "department_count": Department.query.count(),
            "pending_leave_count": pending_leave_count,
            "active_clock_ins": active_clock_ins,
            "recent_payroll_total": sum(record.total_pay for record in payroll_records),
            "recent_payroll_hours": sum(record.hours_worked for record in payroll_records),
        }
        return render_template(
            "dashboard.html",
            active_page="dashboard",
            stats=stats,
            employees=employees,
            recent_records=payroll_records,
            payroll_dates=payroll_dates,
            payroll_totals=payroll_totals,
            department_labels=department_labels,
            department_counts=department_counts,
            attendance_labels=attendance_labels,
            attendance_values=attendance_values,
            my_profile=None,
            my_summary=None,
        )

    my_profile = get_current_employee()
    my_records = (
        PayrollRecord.query.filter_by(employee_id=my_profile.id)
        .order_by(PayrollRecord.work_date.desc())
        .limit(8)
        .all()
        if my_profile
        else []
    )
    summary = {
        "hours": sum(record.hours_worked for record in my_records),
        "regular_hours": sum(record.regular_hours for record in my_records),
        "overtime_hours": sum(record.overtime_hours for record in my_records),
        "total_pay": sum(record.total_pay for record in my_records),
    }
    return render_template(
        "dashboard.html",
        active_page="dashboard",
        stats=None,
        employees=employees,
        recent_records=my_records,
        my_profile=my_profile,
        my_summary=summary,
    )


@employee_bp.route("/employees")
@login_required
def employee_directory():
    search = request.args.get("search", "").strip()
    department_id = request.args.get("department_id", "") or None
    role_filter = request.args.get("role", "") or None

    query = Employee.query.options(joinedload(Employee.department)).outerjoin(User)
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
    if department_id:
        query = query.filter(Employee.department_id == int(department_id))
    if role_filter in Role.CHOICES:
        query = query.filter(User.role == role_filter)

    employees = query.order_by(Employee.last_name, Employee.first_name).all()
    departments = Department.query.order_by(Department.name).all()
    return render_template(
        "employees.html",
        active_page="employees",
        employees=employees,
        departments=departments,
        search=search,
        selected_department=department_id,
        selected_role=role_filter,
        role_choices=Role.CHOICES,
    )


@employee_bp.route("/employees/new", methods=("GET", "POST"))
@roles_required(Role.ADMIN, Role.MANAGER)
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
            hourly_rate = float(request.form.get("hourly_rate", "0"))
            overtime_multiplier = float(request.form.get("overtime_multiplier", "1.5"))
        except ValueError:
            flash("Hourly pay and overtime multiplier must be numeric.", "error")
            return render_template(
                "employee_form.html",
                active_page="add-employee",
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
                hourly_rate=hourly_rate,
                overtime_multiplier=overtime_multiplier,
                date_hired=date.today(),
            )
            db.session.add(employee)
            db.session.commit()
            flash("Employee added successfully.", "success")
            return redirect(url_for("employees.employee_directory"))

    return render_template(
        "employee_form.html",
        active_page="add-employee",
        title="Add Employee",
        employee=None,
        departments=departments,
    )


@employee_bp.route("/employees/<int:employee_id>/edit", methods=("GET", "POST"))
@roles_required(Role.ADMIN, Role.MANAGER)
def edit_employee(employee_id):
    employee = Employee.query.get_or_404(employee_id)
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
            hourly_rate = float(request.form.get("hourly_rate", "0"))
            overtime_multiplier = float(request.form.get("overtime_multiplier", "1.5"))
        except ValueError:
            flash("Hourly pay and overtime multiplier must be numeric.", "error")
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
            employee.hourly_rate = hourly_rate
            employee.overtime_multiplier = overtime_multiplier
            employee.date_hired = employee.date_hired
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
@roles_required(Role.ADMIN)
def delete_employee(employee_id):
    employee = Employee.query.get_or_404(employee_id)
    db.session.delete(employee)
    db.session.commit()
    flash("Employee record deleted successfully.", "success")
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
        schedule = request.form.get("schedule", "").strip()

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
            employee.schedule = schedule
            db.session.commit()
            flash("Profile updated successfully.", "success")
            return redirect(url_for("employees.profile"))

    return render_template("profile.html", active_page="profile", employee=employee)


@employee_bp.route("/payroll", methods=("GET", "POST"))
@login_required
def payroll():
    employees = []
    selected_employee_id = None
    if current_user.is_admin() or current_user.is_manager():
        employees = Employee.query.order_by(Employee.last_name, Employee.first_name).all()
        selected_employee_id = request.args.get("employee_id", "") or None
    else:
        selected_employee_id = current_user.employee_id

    if request.method == "POST":
        if current_user.is_admin() or current_user.is_manager():
            selected_employee_id = request.form.get("employee_id", "")
        if not selected_employee_id:
            flash("Please choose an employee.", "error")
            return redirect(url_for("employees.payroll"))

        employee = Employee.query.get_or_404(int(selected_employee_id))
        work_date = parse_date(request.form.get("work_date", ""))
        try:
            hours_worked = float(request.form.get("hours_worked", "0"))
        except ValueError:
            work_date = None
            hours_worked = None

        if not work_date or hours_worked is None:
            flash("Please provide a valid date and hours value.", "error")
            return redirect(url_for("employees.payroll"))

        notes = request.form.get("notes", "").strip()
        if hours_worked < 0 or hours_worked > 168:
            flash("Hours worked must be between 0 and 168.", "error")
            return redirect(url_for("employees.payroll"))

        record = PayrollRecord.query.filter_by(employee_id=employee.id, work_date=work_date).first()
        if record:
            record.hours_worked = hours_worked
            record.regular_hours, record.overtime_hours, record.total_pay = PayrollRecord.calculate(
                hours_worked, employee.hourly_rate, employee.overtime_multiplier
            )
            record.notes = notes
        else:
            record = PayrollRecord.build(employee, work_date, hours_worked, notes)
            db.session.add(record)
        db.session.commit()
        flash("Payroll record saved successfully.", "success")
        return redirect(url_for("employees.payroll", employee_id=employee.id if employees else None))

    payroll_filters = []
    if selected_employee_id:
        payroll_filters.append(PayrollRecord.employee_id == int(selected_employee_id))

    records = PayrollRecord.query.options(joinedload(PayrollRecord.employee)).filter(*payroll_filters).order_by(
        PayrollRecord.work_date.desc()
    )
    if current_user.is_admin() or current_user.is_manager():
        records = records.all()
    else:
        records = records.filter_by(employee_id=current_user.employee_id).all()

    summary = {
        "hours": sum(record.hours_worked for record in records),
        "regular_hours": sum(record.regular_hours for record in records),
        "overtime_hours": sum(record.overtime_hours for record in records),
        "total_pay": sum(record.total_pay for record in records),
    }
    employee_profile = get_current_employee()
    return render_template(
        "payroll.html",
        active_page="payroll",
        records=records,
        summary=summary,
        employees=employees,
        selected_employee=selected_employee_id,
        employee_profile=employee_profile,
    )


@employee_bp.route("/payroll/<int:record_id>/delete", methods=("POST",))
@roles_required(Role.ADMIN, Role.MANAGER)
def delete_payroll(record_id):
    record = PayrollRecord.query.get_or_404(record_id)
    db.session.delete(record)
    db.session.commit()
    flash("Payroll record removed successfully.", "success")
    return redirect(url_for("employees.payroll", employee_id=record.employee_id))
