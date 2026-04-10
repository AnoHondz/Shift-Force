from datetime import datetime, timedelta

from flask import Blueprint, render_template, request

from ..decorators import roles_required
from ..models import AuditLog, Role
from ..utils import parse_date


audit_bp = Blueprint("audit", __name__, url_prefix="/audit")


@audit_bp.route("/logs")
@roles_required(Role.ADMIN)
def index():
    action_type = request.args.get("action_type", "")
    start_date = parse_date(request.args.get("start_date", ""))
    end_date = parse_date(request.args.get("end_date", ""))

    query = AuditLog.query.order_by(AuditLog.created_at.desc())
    if action_type:
        query = query.filter(AuditLog.action_type == action_type)
    if start_date:
        query = query.filter(AuditLog.created_at >= datetime.combine(start_date, datetime.min.time()))
    if end_date:
        query = query.filter(AuditLog.created_at <= datetime.combine(end_date, datetime.max.time()))

    logs = query.all()
    return render_template(
        "audit_logs.html",
        active_page="audit",
        logs=logs,
        action_types=AuditLog.ACTION_TYPES,
        selected_action=action_type,
        start_date=start_date,
        end_date=end_date,
    )
