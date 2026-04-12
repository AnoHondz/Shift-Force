from datetime import date, datetime

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import joinedload

from ..decorators import roles_required
from ..extensions import db
from ..models import Employee, LeaveRequest, Role, User, create_audit_log, create_notification
from ..utils import get_current_employee, parse_date


leave_bp = Blueprint("leave", __name__, url_prefix="/leave")


@leave_bp.route("/")
@login_required
def index():
    if current_user.has_admin_access():
        requests = LeaveRequest.query.options(joinedload(LeaveRequest.employee)).order_by(LeaveRequest.submitted_at.desc()).all()
    else:
        requests = (
            LeaveRequest.query.options(joinedload(LeaveRequest.employee))
            .filter_by(employee_id=current_user.employee_id)
            .order_by(LeaveRequest.submitted_at.desc())
            .all()
        )
    return render_template(
        "leave.html",
        active_page="leave",
        requests=requests,
        employee=get_current_employee(),
    )


@leave_bp.route("/new", methods=("GET", "POST"))
@login_required
def submit():
    employee = get_current_employee()
    if employee is None:
        flash("Your account is not linked to an employee profile.", "error")
        return redirect(url_for("leave.index"))

    if request.method == "POST":
        request_type = request.form.get("request_type", "").strip()
        start_date = request.form.get("start_date", "").strip()
        end_date = request.form.get("end_date", "").strip()
        reason = request.form.get("reason", "").strip()
        error = None

        start_date_obj = parse_date(start_date)
        end_date_obj = parse_date(end_date)
        if start_date and not start_date_obj or end_date and not end_date_obj:
            error = "Please enter valid start and end dates."

        if error is None:
            if not request_type or not start_date_obj or not end_date_obj or start_date_obj > end_date_obj:
                error = "Leave type and a valid date range are required."
        if error is None:
            leave_request = LeaveRequest(
                employee_id=employee.id,
                request_type=request_type,
                start_date=start_date_obj,
                end_date=end_date_obj,
                reason=reason,
                status="Pending",
                submitted_at=datetime.utcnow(),
            )
            db.session.add(leave_request)
            create_audit_log(
                current_user,
                "leave_submitted",
                "LeaveRequest",
                None,
                f"{employee.full_name()} submitted a {request_type} leave request.",
            )
            admin_users = User.query.filter(User.role.in_(Role.ADMIN_LIKE)).all()
            for user in admin_users:
                create_notification(
                    user,
                    "Pending leave approval",
                    f"{employee.full_name()} submitted a {request_type} request from {start_date_obj} to {end_date_obj}.",
                    "/leave",
                    "approval",
                )
            db.session.commit()
            flash("Leave request submitted.", "success")
            return redirect(url_for("leave.index"))

        flash(error, "error")

    return render_template("leave_form.html", active_page="leave", employee=employee)


@leave_bp.route("/<int:request_id>/review", methods=("POST",))
@roles_required(*Role.ADMIN_LIKE)
def review(request_id):
    action = request.form.get("action")
    leave_request = LeaveRequest.query.get_or_404(request_id)
    if action not in {"approve", "reject"}:
        flash("Invalid action.", "error")
        return redirect(url_for("leave.index"))

    leave_request.status = "Approved" if action == "approve" else "Rejected"
    leave_request.reviewed_by_id = current_user.id
    leave_request.reviewed_at = datetime.utcnow()
    create_audit_log(
        current_user,
        "leave_reviewed",
        "LeaveRequest",
        str(leave_request.id),
        f"Leave request for {leave_request.employee.full_name()} marked {leave_request.status.lower()}.",
    )
    if leave_request.employee.user:
        create_notification(
            leave_request.employee.user,
            "Leave request updated",
            f"Your leave request was {leave_request.status.lower()} by {current_user.username}.",
            "/leave",
            "info" if action == "approve" else "warning",
        )
    db.session.commit()
    flash(f"Leave request {leave_request.status.lower()}.", "success")
    return redirect(url_for("leave.index"))
