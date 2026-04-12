from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..extensions import db
from ..models import LeaveRequest, Notification, PayrollRecord
from ..utils import parse_positive_int


notifications_bp = Blueprint("notifications", __name__, url_prefix="/notifications")


@notifications_bp.route("/")
@login_required
def index():
    view = request.args.get("view", "all")
    page = parse_positive_int(request.args.get("page"), default=1)
    per_page = parse_positive_int(request.args.get("per_page"), default=20, maximum=100)
    query = Notification.query.order_by(Notification.created_at.desc())
    if current_user.has_admin_access():
        if view not in {"all", "pending", "alerts"}:
            view = "all"
        if view == "pending":
            query = query.filter(Notification.category.in_(["warning", "approval"]))
        elif view == "alerts":
            query = query.filter(Notification.category.in_(["warning", "security"]))
    else:
        if view not in {"all", "unread"}:
            view = "all"
        query = query.filter_by(user_id=current_user.id)
        if view == "unread":
            query = query.filter_by(is_read=False)

    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    notifications = pagination.items
    pending_leave = LeaveRequest.query.filter_by(status="Pending").count() if current_user.has_admin_access() else 0
    pending_hours = PayrollRecord.query.filter_by(status="Pending").count() if current_user.has_admin_access() else 0

    return render_template(
        "notifications.html",
        active_page="notifications",
        notifications=notifications,
        pagination=pagination,
        per_page=per_page,
        current_view=view,
        pending_leave=pending_leave,
        pending_hours=pending_hours,
        admin_view=current_user.has_admin_access(),
    )


@notifications_bp.route("/<int:notification_id>/read", methods=("POST",))
@login_required
def mark_read(notification_id):
    notification = Notification.query.get_or_404(notification_id)
    if notification.user_id != current_user.id and not current_user.has_admin_access():
        flash("You do not have permission to update that notification.", "error")
        return redirect(url_for("notifications.index"))

    notification.is_read = True
    db.session.commit()
    flash("Notification marked as read.", "success")
    return redirect(url_for("notifications.index"))


@notifications_bp.route("/mark-all", methods=("POST",))
@login_required
def mark_all_read():
    if current_user.has_admin_access():
        Notification.query.filter_by(is_read=False).update({"is_read": True})
    else:
        Notification.query.filter_by(user_id=current_user.id, is_read=False).update({"is_read": True})
    db.session.commit()
    flash("All notifications marked as read.", "success")
    return redirect(url_for("notifications.index"))
