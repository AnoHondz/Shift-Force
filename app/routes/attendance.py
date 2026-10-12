from datetime import timezone
from zoneinfo import ZoneInfo

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import joinedload

from ..extensions import db
from ..models import Attendance, Employee, Role, create_audit_log, utcnow
from ..utils import get_current_employee, parse_date, parse_positive_int

attendance_bp = Blueprint("attendance", __name__, url_prefix="/attendance")


@attendance_bp.route("/", methods=("GET", "POST"))
@login_required
def index():
    employee = get_current_employee()
    selected_employee_id = request.args.get("employee_id", "") or None
    start_date = request.args.get("start_date", "")
    end_date = request.args.get("end_date", "")

    employees = []
    if current_user.has_admin_access():
        employees = (
            Employee.query.filter_by(archived=False)
            .order_by(Employee.last_name, Employee.first_name)
            .all()
        )

    if request.method == "POST":
        action = request.form.get("action")
        if action not in {"clock_in", "clock_out"}:
            abort(400, description="Choose clock in or clock out.")
        if current_user.has_admin_access() and current_user.role not in Role.LEAVE_REVIEW:
            abort(403)
        if current_user.has_admin_access():
            selected_employee_id = request.form.get("employee_id", "")
            if not selected_employee_id:
                flash("Please select an employee for attendance actions.", "error")
                return redirect(url_for("attendance.index"))
            selected_employee_id = (
                parse_positive_int(selected_employee_id, default=None)
                if selected_employee_id
                else None
            )
            if not selected_employee_id:
                flash("Invalid employee selection.", "error")
                return redirect(url_for("attendance.index"))
        else:
            selected_employee_id = current_user.employee_id

        if not selected_employee_id:
            abort(400, description="Your account needs an employee profile.")
        target_employee = db.get_or_404(Employee, int(selected_employee_id))
        if target_employee.archived:
            abort(400, description="Archived employees cannot clock in or out.")
        if action == "clock_in":
            active_record = Attendance.query.filter_by(
                employee_id=target_employee.id, clock_out_at=None
            ).first()
            if active_record:
                flash("You must clock out before starting a new shift.", "error")
                return redirect(url_for("attendance.index"))

            attendance = Attendance(
                employee_id=target_employee.id,
                clock_in_at=utcnow(),
                date=utcnow()
                .replace(tzinfo=timezone.utc)
                .astimezone(ZoneInfo(current_app.config.get("WORKFORCE_TIMEZONE", "UTC")))
                .date(),
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
            active_record = Attendance.query.filter_by(
                employee_id=target_employee.id, clock_out_at=None
            ).first()
            if not active_record:
                flash("No active clock-in found for this employee.", "error")
                return redirect(url_for("attendance.index"))

            try:
                active_record.complete_shift(utcnow())
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

    attendance_query = Attendance.query.options(joinedload(Attendance.employee)).order_by(
        Attendance.clock_in_at.desc()
    )
    if current_user.is_employee():
        attendance_query = attendance_query.filter_by(employee_id=current_user.employee_id)
    else:
        if selected_employee_id:
            selected_employee_id = parse_positive_int(selected_employee_id, default=None)
            if selected_employee_id:
                attendance_query = attendance_query.filter_by(employee_id=selected_employee_id)
        parsed_start = parse_date(start_date)
        parsed_end = parse_date(end_date)
        if parsed_start:
            attendance_query = attendance_query.filter(Attendance.date >= parsed_start)
        if parsed_end:
            attendance_query = attendance_query.filter(Attendance.date <= parsed_end)

    records = attendance_query.all()
    total_records = (
        Attendance.query.count()
        if current_user.has_admin_access()
        else Attendance.query.filter_by(employee_id=current_user.employee_id).count()
    )
    total_hours = sum(record.hours_worked for record in records if record.hours_worked)
    if current_user.has_admin_access():
        active_clock_ins = Attendance.query.filter_by(clock_out_at=None).count()
    else:
        active_clock_ins = Attendance.query.filter_by(
            employee_id=current_user.employee_id, clock_out_at=None
        ).count()
    active_record = None
    if current_user.is_employee():
        active_record = Attendance.query.filter_by(
            employee_id=current_user.employee_id, clock_out_at=None
        ).first()

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
