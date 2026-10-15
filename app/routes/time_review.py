"""Preview, reconcile and prepare pending timesheets from actual clock records."""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..extensions import db, limiter
from ..models import Employee, PayrollRecord, Role, create_audit_log, create_notification
from ..time_review import serialize_command, time_preview
from ..utils import get_current_employee, store_today
from .planning import action_key, week_value
from .workplace import employees, workplace_zone

time_review_bp = Blueprint("time_review", __name__)


@time_review_bp.route("/payroll/clock-hours", methods=("GET", "POST"))
@login_required
@limiter.limit("10 per minute", key_func=action_key, methods=["POST"])
def clock_hours():
    can_prepare = current_user.role in Role.PAYROLL_REVIEW
    if request.method == "POST":
        if not can_prepare:
            abort(403)
        serialize_command()
    selected = request.values.get("employee_id", "")
    staff = employees() if can_prepare else []
    if can_prepare and selected:
        if (
            not selected.isascii()
            or not selected.isdigit()
            or len(selected) > 18
            or int(selected) < 1
        ):
            abort(400, description="Choose a valid employee.")
        employee = db.get_or_404(Employee, int(selected))
    else:
        employee = get_current_employee()
    week = week_value()
    preview = time_preview(employee, week, workplace_zone(), store_today()) if employee else None
    if request.method == "POST":
        if employee is None or not selected:
            abort(400, description="Choose an employee to prepare hours.")
        if request.form.get("source_token") != preview["token"]:
            abort(
                409,
                description="Clock records or payroll changed. Refresh the preview before preparing hours.",
            )
        if request.form.get("confirmed") != "1":
            abort(
                400,
                description="Confirm that these clock intervals include the payable work and breaks.",
            )
        if preview["issues"]:
            abort(
                409,
                description="Resolve the listed clock/payroll issues before preparing this week.",
            )
        changed = 0
        for item in preview["days"]:
            record = item["record"]
            if not item["actual"] and record is None:
                continue
            if record and record.status == "Voided":
                abort(
                    409,
                    description="A voided entry needs an explicit manual correction; preparation will not revive it.",
                )
            if (
                record
                and record.hours_worked == item["actual"]
                and record.attendance_fingerprint == preview["signature"]
                and record.status in {"Pending", "Reviewed"}
            ):
                continue
            old = str(record.hours_worked) if record else "new"
            if record is None:
                record = PayrollRecord.build(
                    employee,
                    item["date"],
                    item["actual"],
                    "Prepared from reviewed clock intervals.",
                )
                db.session.add(record)
            else:
                record.hours_worked = item["actual"]
                record.status = "Pending"
                record.reviewed_by_id = None
                record.reviewed_at = None
            record.attendance_fingerprint = preview["signature"]
            db.session.flush()
            create_audit_log(
                current_user,
                "attendance_timesheets_prepared",
                "PayrollRecord",
                str(record.id),
                f"Reviewed clock hours for {item['date']}: {old} to {item['actual']}; source fingerprint {preview['signature']}.",
            )
            changed += 1
        try:
            PayrollRecord.recalculate_week(employee.id, week)
        except ValueError as exc:
            db.session.rollback()
            abort(409, description=str(exc))
        if changed and employee.user:
            create_notification(
                employee.user,
                "Clock hours prepared",
                "Your recorded clock intervals were prepared as pending daily timesheets. Review the entries; wages still need approval.",
                "/payroll",
                "approval",
            )
        db.session.commit()
        flash(
            f"Prepared {changed} pending entries."
            if changed
            else "These clock hours are already prepared. Nothing changed.",
            "success",
        )
        return redirect(
            url_for("time_review.clock_hours", employee_id=employee.id, week=week.isoformat())
        )
    return render_template(
        "clock_hours.html",
        preview=preview,
        employees=staff,
        can_prepare=can_prepare,
        selected_employee=employee.id if employee else None,
        week=week,
        active_page="payroll",
        page_title="Review clock hours",
    )
