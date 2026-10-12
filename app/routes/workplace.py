"""Everyday workspace and manager tools, with shared persistent coworker chat."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import or_

from ..extensions import db, limiter
from ..models import (
    Attendance,
    Employee,
    Role,
    ScheduledShift,
    TeamMessage,
    create_audit_log,
    utcnow,
)
from ..utils import get_current_employee

workplace_bp = Blueprint("workplace", __name__)


def workplace_zone():
    return ZoneInfo(current_app.config.get("WORKFORCE_TIMEZONE", "UTC"))


def local_input(value):
    if value is None:
        return ""
    return (
        value.replace(tzinfo=timezone.utc).astimezone(workplace_zone()).strftime("%Y-%m-%dT%H:%M")
    )


def parse_shift_time(value):
    try:
        parsed = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValueError("Enter a valid date and time.") from None
    if parsed.tzinfo:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    candidates = set()
    for fold in (0, 1):
        candidate = parsed.replace(tzinfo=workplace_zone(), fold=fold).astimezone(timezone.utc)
        if candidate.astimezone(workplace_zone()).replace(tzinfo=None) == parsed:
            candidates.add(candidate.replace(tzinfo=None))
    if len(candidates) != 1:
        raise ValueError("This time falls during a clock change. Choose an unambiguous local time.")
    return candidates.pop()


def require_manager(write=False):
    allowed = Role.LEAVE_REVIEW if write else Role.ADMIN_LIKE
    if current_user.role not in allowed:
        abort(403)


def require_coworker():
    employee = get_current_employee()
    if employee is None or employee.archived:
        abort(403, description="An active employee profile is needed to use team chat.")
    return employee


def employees():
    return (
        Employee.query.filter_by(archived=False)
        .order_by(Employee.last_name, Employee.first_name)
        .all()
    )


def selected_employee():
    try:
        employee_id = int(request.form.get("employee_id", ""))
    except ValueError:
        abort(400, description="Choose an employee.")
    employee = db.get_or_404(Employee, employee_id)
    if employee.archived:
        abort(400, description="Choose an active employee.")
    return employee


def verify_version(record):
    if request.form.get("version_id") != str(record.version_id):
        abort(409, description="This record changed. Refresh it before saving.")


def validate_interval(start, end):
    if end <= start or end - start > timedelta(hours=24):
        raise ValueError("End time must follow start time and the shift must be at most 24 hours.")


def latest_messages():
    return list(reversed(TeamMessage.query.order_by(TeamMessage.id.desc()).limit(25).all()))


def render_today():
    employee = get_current_employee()
    now = utcnow()
    upcoming = []
    if employee:
        upcoming = (
            ScheduledShift.query.filter(
                ScheduledShift.employee_id == employee.id,
                ScheduledShift.cancelled.is_(False),
                ScheduledShift.ends_at >= now,
            )
            .order_by(ScheduledShift.starts_at)
            .limit(5)
            .all()
        )
    return render_template(
        "today.html",
        active_page="today",
        page_title="Today",
        shift_employee=employee,
        active_shift=Attendance.query.filter_by(
            employee_id=current_user.employee_id, clock_out_at=None
        ).first(),
        upcoming=upcoming,
        messages=latest_messages() if employee and not employee.archived else [],
    )


@workplace_bp.route("/manage")
@login_required
def manage():
    require_manager()
    return render_template("manage.html", active_page="manage", page_title="Manage workplace")


@workplace_bp.route("/schedule", methods=("GET", "POST"))
@login_required
def schedule():
    if request.method == "POST":
        require_manager(write=True)
        employee = selected_employee()
        record_id = request.form.get("shift_id")
        record = db.get_or_404(ScheduledShift, record_id) if record_id else None
        if record:
            verify_version(record)
            if record.cancelled:
                abort(400, description="This shift has already been cancelled.")
        try:
            start = parse_shift_time(request.form.get("starts_at"))
            end = parse_shift_time(request.form.get("ends_at"))
            validate_interval(start, end)
            notes = request.form.get("notes", "").strip()
            if len(notes) > 255:
                raise ValueError("Shift notes must be at most 255 characters.")
            overlap = ScheduledShift.query.filter(
                ScheduledShift.employee_id == employee.id,
                ScheduledShift.cancelled.is_(False),
                ScheduledShift.starts_at < end,
                ScheduledShift.ends_at > start,
            )
            if record:
                overlap = overlap.filter(ScheduledShift.id != record.id)
            if overlap.first():
                raise ValueError("This employee already has a shift during those hours.")
        except ValueError as exc:
            abort(400, description=str(exc))
        before = (
            f"{record.employee_id}: {record.starts_at} to {record.ends_at}; {record.notes}"
            if record
            else "No shift"
        )
        action = "schedule_updated" if record else "schedule_created"
        if not record:
            record = ScheduledShift()
            db.session.add(record)
        record.employee_id, record.starts_at, record.ends_at, record.notes = (
            employee.id,
            start,
            end,
            notes,
        )
        db.session.flush()
        create_audit_log(
            current_user,
            action,
            "ScheduledShift",
            str(record.id),
            f"Before: {before}. After: {employee.id}: {start} to {end}; {notes} (UTC).",
        )
        db.session.commit()
        flash("Schedule saved.", "success")
        return redirect(url_for("workplace.schedule"))
    now = utcnow()
    start_local = (
        now.replace(tzinfo=timezone.utc)
        .astimezone(workplace_zone())
        .replace(hour=0, minute=0, second=0, microsecond=0)
    )
    try:
        days = int(request.args.get("days", 0))
        if abs(days) > 3650:
            raise ValueError
    except ValueError:
        abort(400, description="Choose a valid schedule week.")
    start_local += timedelta(days=days - start_local.weekday())
    start = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end = (start_local + timedelta(days=7)).astimezone(timezone.utc).replace(tzinfo=None)
    query = ScheduledShift.query.filter(
        ScheduledShift.ends_at > start,
        ScheduledShift.starts_at < end,
        ScheduledShift.cancelled.is_(False),
    )
    if not current_user.has_admin_access():
        query = query.filter_by(employee_id=current_user.employee_id)
    editing = None
    if request.args.get("edit"):
        require_manager(write=True)
        editing = db.get_or_404(ScheduledShift, request.args["edit"])
        if editing.cancelled:
            abort(404)
    return render_template(
        "schedule.html",
        active_page="schedule",
        page_title="Schedule",
        shifts=query.order_by(ScheduledShift.starts_at).limit(500).all(),
        employees=employees() if current_user.role in Role.LEAVE_REVIEW else [],
        editing=editing,
        local_input=local_input,
        timezone_name=str(workplace_zone()),
        week=start_local.date(),
        days=days,
    )


@workplace_bp.post("/schedule/<int:shift_id>/cancel")
@login_required
def cancel_shift(shift_id):
    require_manager(write=True)
    record = db.get_or_404(ScheduledShift, shift_id)
    verify_version(record)
    if record.cancelled:
        abort(400, description="This shift has already been cancelled.")
    record.cancelled = True
    create_audit_log(
        current_user,
        "schedule_cancelled",
        "ScheduledShift",
        str(record.id),
        f"Cancelled {record.employee.full_name()}: {record.starts_at} to {record.ends_at} (UTC).",
    )
    db.session.commit()
    flash("Shift cancelled.", "success")
    return redirect(url_for("workplace.schedule"))


@workplace_bp.route("/manage/punches", methods=("GET", "POST"))
@login_required
def punches():
    require_manager()
    record = None
    if request.method == "POST":
        require_manager(write=True)
        if request.form.get("record_id"):
            record = db.get_or_404(Attendance, request.form["record_id"])
            verify_version(record)
            employee = record.employee
            if employee.archived:
                abort(400, description="Archived employees cannot have punches changed.")
        else:
            employee = selected_employee()
        try:
            reason = request.form.get("reason", "").strip()
            if not 3 <= len(reason) <= 500:
                raise ValueError("Add a correction reason between 3 and 500 characters.")
            start = parse_shift_time(request.form.get("clock_in_at"))
            end = (
                parse_shift_time(request.form["clock_out_at"])
                if request.form.get("clock_out_at")
                else None
            )
            now = utcnow()
            if start > now or (end and end > now):
                raise ValueError("Actual punches cannot be in the future.")
            if end:
                validate_interval(start, end)
            overlap = Attendance.query.filter(
                Attendance.employee_id == employee.id,
                or_(Attendance.clock_out_at.is_(None), Attendance.clock_out_at > start),
            )
            if end:
                overlap = overlap.filter(Attendance.clock_in_at < end)
            if record:
                overlap = overlap.filter(Attendance.id != record.id)
            if overlap.first():
                raise ValueError("These punches overlap another shift for this employee.")
        except ValueError as exc:
            abort(400, description=str(exc))
        before = (
            f"{record.clock_in_at} to {record.clock_out_at}; {record.hours_worked} hours"
            if record
            else "No punches"
        )
        if record is None:
            record = Attendance(employee_id=employee.id)
            db.session.add(record)
        record.clock_in_at, record.clock_out_at = start, None
        record.hours_worked, record.status = 0, "In Progress"
        record.date = start.replace(tzinfo=timezone.utc).astimezone(workplace_zone()).date()
        if end:
            record.complete_shift(end)
        db.session.flush()
        create_audit_log(
            current_user,
            "attendance_corrected",
            "Attendance",
            str(record.id),
            f"{employee.full_name()}. Before: {before}. After: {start} to {end}; {record.hours_worked} hours (UTC). Reason: {reason}",
        )
        db.session.commit()
        flash("Punches saved. Review any affected payroll entry separately.", "success")
        return redirect(url_for("workplace.punches"))
    if request.args.get("edit"):
        require_manager(write=True)
        record = db.get_or_404(Attendance, request.args["edit"])
    page = request.args.get("page", 1, type=int)
    records = Attendance.query.order_by(Attendance.clock_in_at.desc()).paginate(
        page=page, per_page=25
    )
    return render_template(
        "punches.html",
        active_page="manage",
        page_title="Fix missed punches",
        records=records,
        editing=record,
        employees=employees(),
        local_input=local_input,
        timezone_name=str(workplace_zone()),
    )


@workplace_bp.get("/chat")
@login_required
def chat():
    require_coworker()
    return render_template(
        "chat.html", active_page="chat", page_title="Team chat", messages=latest_messages()
    )


def message_data(message):
    employee = message.user.employee
    return dict(
        id=message.id,
        author=employee.full_name() if employee else message.user.username,
        body=message.body,
        created_at=message.created_at.replace(tzinfo=timezone.utc).isoformat(),
    )


@workplace_bp.get("/api/chat/messages")
@login_required
def messages():
    require_coworker()
    after = request.args.get("after", "0")
    if not after.isdigit() or len(after) > 18:
        abort(400, description="Invalid message cursor.")
    cursor = int(after)
    rows = (
        TeamMessage.query.filter(TeamMessage.id > cursor).order_by(TeamMessage.id).limit(100).all()
        if cursor
        else latest_messages()
    )
    return jsonify(messages=[message_data(row) for row in rows])


@workplace_bp.post("/api/chat/messages")
@login_required
@limiter.limit("30 per minute", key_func=lambda: str(current_user.id))
def send_message():
    require_coworker()
    payload = request.get_json(silent=True)
    body = payload.get("body") if isinstance(payload, dict) else None
    if not isinstance(body, str) or not 1 <= len(body.strip()) <= 2000:
        abort(400, description="Write a message between 1 and 2,000 characters.")
    message = TeamMessage(user_id=current_user.id, body=body.strip())
    db.session.add(message)
    db.session.commit()
    return jsonify(message=message_data(message)), 201
