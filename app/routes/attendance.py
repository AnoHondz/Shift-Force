from datetime import datetime, date

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import joinedload

from ..decorators import roles_required
from ..extensions import db
from ..models import Attendance, Employee, Role, create_audit_log
from ..utils import get_current_employee, parse_date


attendance_bp = Blueprint("attendance", __name__, url_prefix="/attendance")


@attendance_bp.route("/", methods=("GET", "POST"))
@login_required
def index():
    employee = get_current_employee()
    selected_employee_id = request.args.get("employee_id", "") or None
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")

    employees = []
    if current_user.is_admin() or current_user.is_manager():
        employees = Employee.query.order_by(Employee.last_name, Employee.first_name).all()

    if request.method == "POST":
        action = request.form.get("action")
        if current_user.is_admin() or current_user.is_manager():
            selected_employee_id = request.form.get("employee_id", "")
            if not selected_employee_id:
                flash("Please select an employee for attendance actions.", "error")
                return redirect(url_for("attendance.index"))
        else:
            selected_employee_id = current_user.employee_id

        target_employee = Employee.query.get_or_404(int(selected_employee_id))
        if action == "clock_in":
            active_record = Attendance.query.filter_by(employee_id=target_employee.id, clock_out_at=None).first()
            if active_record:
                flash("You must clock out before starting a new shift.", "error")
                return redirect(url_for("attendance.index"))

            attendance = Attendance(
                employee_id=target_employee.id,
                clock_in_at=datetime.utcnow(),
                date=date.today(),
                status="In Progress",
            )
            db.session.add(attendance)
            create_audit_log(
                current_user,
                "attendance_clocked_in",
                "Attendance",
                str(target_employee.id),
                f"{target_employee.full_name()} clocked in.",
            )
            db.session.commit()
            flash("Clocked in successfully.", "success")
            return redirect(url_for("attendance.index"))

        if action == "clock_out":
            active_record = Attendance.query.filter_by(employee_id=target_employee.id, clock_out_at=None).first()
            if not active_record:
                flash("No active clock-in found for this employee.", "error")
                return redirect(url_for("attendance.index"))

            try:
                active_record.complete_shift(datetime.utcnow())
            except ValueError as exc:
                flash(str(exc), "error")
                return redirect(url_for("attendance.index"))

            create_audit_log(
                current_user,
                "attendance_clocked_out",
                "Attendance",
                str(target_employee.id),
                f"{target_employee.full_name()} clocked out after {active_record.hours_worked} hours.",
            )
            db.session.commit()
            flash("Clocked out successfully.", "success")
            return redirect(url_for("attendance.index"))

    attendance_query = Attendance.query.options(joinedload(Attendance.employee)).order_by(Attendance.clock_in_at.desc())
    if current_user.is_employee():
        attendance_query = attendance_query.filter_by(employee_id=current_user.employee_id)
    else:
        if selected_employee_id:
            attendance_query = attendance_query.filter_by(employee_id=int(selected_employee_id))
        parsed_start = parse_date(start_date)
        parsed_end = parse_date(end_date)
        if parsed_start:
            attendance_query = attendance_query.filter(Attendance.date >= parsed_start)
        if parsed_end:
            attendance_query = attendance_query.filter(Attendance.date <= parsed_end)

    records = attendance_query.all()
    total_records = Attendance.query.count() if current_user.is_admin() or current_user.is_manager() else Attendance.query.filter_by(employee_id=current_user.employee_id).count()
    total_hours = sum(record.hours_worked for record in records if record.hours_worked)
    active_clock_ins = Attendance.query.filter_by(clock_out_at=None).count()
    active_record = None
    if current_user.is_employee():
        active_record = Attendance.query.filter_by(employee_id=current_user.employee_id, clock_out_at=None).first()

    return render_template(
        "attendance.html",
        active_page="attendance",
        employees=employees,
        records=records,
        selected_employee=selected_employee_id,
        start_date=start_date,
        end_date=end_date,
        total_records=total_records,
        total_hours=total_hours,
        active_clock_ins=active_clock_ins,
        active_record=active_record,
        employee=employee,
    )
