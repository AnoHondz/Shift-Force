from datetime import datetime

from flask import Blueprint, render_template, request
from sqlalchemy import or_

from ..decorators import roles_required
from ..models import AuditLog, Role, User
from ..utils import parse_date, parse_positive_int

audit_bp = Blueprint("audit", __name__, url_prefix="/audit")


@audit_bp.route("/logs")
@roles_required(*Role.ADMIN_LIKE)
def index():
    action_type = request.args.get("action_type", "")
    search = request.args.get("search", "").strip()
    start_date = parse_date(request.args.get("start_date", ""))
    end_date = parse_date(request.args.get("end_date", ""))
    severity = request.args.get("severity", "")
    page = parse_positive_int(request.args.get("page"), default=1)
    per_page = parse_positive_int(request.args.get("per_page"), default=25, maximum=100)

    query = AuditLog.query
    if action_type:
        query = query.filter(AuditLog.action_type == action_type)
    if search:
        search_like = f"%{search}%"
        query = query.join(User, isouter=True).filter(
            or_(
                AuditLog.description.ilike(search_like),
                AuditLog.target_type.ilike(search_like),
                User.username.ilike(search_like),
            )
        )
    if start_date:
        query = query.filter(
            AuditLog.created_at >= datetime.combine(start_date, datetime.min.time())
        )
    if end_date:
        query = query.filter(AuditLog.created_at <= datetime.combine(end_date, datetime.max.time()))
    if severity:
        if severity == "critical":
            query = query.filter(
                AuditLog.action_type.in_(["employee_deleted", "login_failed", "security_alert"])
            )
        elif severity == "high":
            query = query.filter(
                AuditLog.action_type.in_(["employee_updated", "leave_reviewed", "payroll_deleted"])
            )
        elif severity == "info":
            query = query.filter(
                AuditLog.action_type.in_(
                    ["profile_updated", "attendance_clocked_in", "attendance_clocked_out"]
                )
            )

    pagination = query.order_by(AuditLog.created_at.desc()).paginate(
        page=page, per_page=per_page, error_out=False
    )
    logs = pagination.items
    return render_template(
        "audit_logs.html",
        active_page="audit",
        logs=logs,
        pagination=pagination,
        per_page=per_page,
        action_types=AuditLog.ACTION_TYPES,
        selected_action=action_type,
        selected_search=search,
        start_date=start_date,
        end_date=end_date,
        selected_severity=severity,
    )
