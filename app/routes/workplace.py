"""Everyday workspace and manager tools, with shared persistent coworker chat."""

import re
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
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
    send_file,
    url_for,
)
from flask_login import current_user, login_required
from sqlalchemy import or_
from werkzeug.exceptions import HTTPException

from ..chat_images import normalize_picture
from ..extensions import db, limiter
from ..models import (
    Attendance,
    ChatAttachment,
    Employee,
    LeaveRequest,
    Role,
    ScheduledShift,
    TeamChannel,
    TeamMessage,
    create_audit_log,
    create_notification,
    utcnow,
)
from ..scheduling import (
    day_boundary,
    local_datetime,
    monday,
    repeat_intervals,
    selected_week,
    simple_intervals,
    validate_availability,
    week_calendar,
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
    value = request.form.get("employee_id", "")
    if not value.isascii() or not value.isdigit() or len(value) > 18 or int(value) < 1:
        abort(400, description="Choose an employee.")
    employee = db.get_or_404(Employee, int(value))
    if employee.archived:
        abort(400, description="Choose an active employee.")
    return employee


def verify_version(record):
    if request.form.get("version_id") != str(record.version_id):
        abort(409, description="This record changed. Refresh it before saving.")


def validate_interval(start, end):
    if end <= start or end - start > timedelta(hours=24):
        raise ValueError("End time must follow start time and the shift must be at most 24 hours.")


def latest_messages(channel_id=1, before=None):
    query = TeamMessage.query.filter_by(channel_id=channel_id)
    if before:
        query = query.filter(TeamMessage.id < before)
    return list(reversed(query.order_by(TeamMessage.id.desc()).limit(25).all()))


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
    )


@workplace_bp.route("/manage")
@login_required
def manage():
    require_manager()
    return render_template("manage.html", active_page="manage", page_title="Manage workplace")


def schedule_staff_id():
    staff_id = None
    if current_user.has_admin_access() and request.values.get("staff"):
        value = request.values["staff"]
        if not value.isascii() or not value.isdigit() or len(value) > 18:
            abort(400, description="Choose a valid employee filter.")
        staff_id = int(value)
        db.get_or_404(Employee, staff_id)
    return staff_id


def schedule_context(week, editing=None, error=None):
    zone = workplace_zone()
    today = local_datetime(utcnow(), zone).date()
    staff = employees() if current_user.has_admin_access() else []
    staff_id = schedule_staff_id()
    first, last = day_boundary(week, zone), day_boundary(week + timedelta(days=7), zone)
    shifts_query = ScheduledShift.query.filter(
        ScheduledShift.ends_at > first,
        ScheduledShift.starts_at < last,
        ScheduledShift.cancelled.is_(False),
    )
    leaves_query = LeaveRequest.query.filter(
        LeaveRequest.status == "Approved",
        LeaveRequest.start_date <= week + timedelta(days=6),
        LeaveRequest.end_date >= week,
    )
    visible_employee = staff_id if current_user.has_admin_access() else current_user.employee_id
    if visible_employee is not None:
        shifts_query = shifts_query.filter_by(employee_id=visible_employee)
        leaves_query = leaves_query.filter_by(employee_id=visible_employee)
    elif not current_user.has_admin_access():
        # An unlinked account must never inherit the full workplace view.
        shifts_query = shifts_query.filter_by(employee_id=-1)
        leaves_query = leaves_query.filter_by(employee_id=-1)
    shifts = shifts_query.order_by(ScheduledShift.starts_at, ScheduledShift.id).all()
    leaves = leaves_query.order_by(LeaveRequest.employee_id, LeaveRequest.start_date).all()
    calendar, hours = week_calendar(shifts, leaves, week, today, zone)
    default_day = today if week <= today < week + timedelta(days=7) else week
    if request.args.get("on"):
        try:
            chosen_week = selected_week(request.args["on"], None, today)
            if chosen_week != week:
                raise ValueError("Choose a day in the displayed week.")
            default_day = date.fromisoformat(request.args["on"])
        except ValueError as exc:
            abort(400, description=str(exc))
    values = dict(
        employee_id=str(editing.employee_id) if editing else str(staff_id or ""),
        starts_at=local_input(editing.starts_at) if editing else default_day.isoformat() + "T09:00",
        ends_at=local_input(editing.ends_at) if editing else default_day.isoformat() + "T17:00",
        notes=editing.notes if editing else "",
        version_id=str(editing.version_id) if editing else "",
    )
    if request.method == "POST":
        values.update({key: request.form.get(key, "") for key in values})
    values.update(
        shift_date=values["starts_at"][:10],
        start_time=values["starts_at"][11:16],
        end_time=values["ends_at"][11:16],
    )
    chosen_days = [str(default_day.weekday())]
    if request.method == "POST":
        if "simple_schedule" in request.form:
            values.update(
                {key: request.form.get(key, "") for key in ("shift_date", "start_time", "end_time")}
            )
            chosen_days = request.form.getlist("shift_days")
        else:
            chosen_days = request.form.getlist("repeat_days")
            try:
                chosen_days = [
                    *chosen_days,
                    str(date.fromisoformat(values["shift_date"]).weekday()),
                ]
            except ValueError:
                pass
    return dict(
        active_page="schedule",
        page_title="Schedule",
        week=week,
        previous_week=week - timedelta(days=7),
        next_week=week + timedelta(days=7),
        days=(week - monday(today)).days,
        calendar=calendar,
        shift_count=len(shifts),
        total_hours=hours,
        employees=staff,
        staff_id=staff_id,
        editing=editing,
        timezone_name=str(zone),
        form_values=values,
        form_error=error,
        chosen_days=chosen_days,
        original_start=local_input(editing.starts_at)[11:16] if editing else "",
        original_end=local_input(editing.ends_at)[11:16] if editing else "",
        original_overnight=bool(
            editing and local_input(editing.ends_at)[:10] > local_input(editing.starts_at)[:10]
        ),
    )


@workplace_bp.route("/schedule", methods=("GET", "POST"))
@login_required
def schedule():
    if request.method == "POST":
        require_manager(write=True)
    try:
        week = selected_week(
            request.values.get("week"),
            request.args.get("days"),
            local_datetime(utcnow(), workplace_zone()).date(),
        )
    except ValueError as exc:
        abort(400, description=str(exc))
    record = None
    if request.method == "POST":
        try:
            record_id = request.form.get("shift_id")
            record = db.get_or_404(ScheduledShift, record_id) if record_id else None
            if record:
                verify_version(record)
                if record.cancelled:
                    raise ValueError(
                        "This shift was cancelled. Refresh the schedule before editing."
                    )
            employee = selected_employee()
            if "simple_schedule" in request.form:
                intervals = simple_intervals(
                    week,
                    request.form,
                    parse_shift_time,
                    editing=record is not None,
                    original=(local_input(record.starts_at), local_input(record.ends_at))
                    if record
                    else None,
                )
                start, end = intervals[0]
            else:
                start = parse_shift_time(request.form.get("starts_at"))
                end = parse_shift_time(request.form.get("ends_at"))
                validate_interval(start, end)
                intervals = repeat_intervals(
                    start,
                    end,
                    request.form.getlist("repeat_days"),
                    workplace_zone(),
                    parse_shift_time,
                    editing=record is not None,
                )
            notes = request.form.get("notes", "").strip()
            if len(notes) > 255:
                raise ValueError("Shift notes must be at most 255 characters.")
            for index, (shift_start, shift_end) in enumerate(intervals):
                validate_interval(shift_start, shift_end)
                selected_week(
                    local_datetime(shift_start, workplace_zone()).date().isoformat(),
                    None,
                    local_datetime(utcnow(), workplace_zone()).date(),
                )
                if index and intervals[index - 1][1] > shift_start:
                    raise ValueError(
                        "These repeated shifts overlap each other. Choose shorter hours."
                    )
                validate_availability(
                    employee.id,
                    shift_start,
                    shift_end,
                    workplace_zone(),
                    exclude_id=record.id if record else None,
                )
        except (ValueError, OverflowError, HTTPException) as exc:
            if isinstance(exc, HTTPException):
                if exc.code not in {400, 409}:
                    raise
                status, message = exc.code, exc.description
            else:
                status = 400
                message = (
                    "Choose valid shift dates." if isinstance(exc, OverflowError) else str(exc)
                )
            return render_template(
                "schedule.html", **schedule_context(week, record, message)
            ), status
        old_employee = record.employee if record else None
        old_interval = (
            (record.employee_id, record.starts_at, record.ends_at, record.notes) if record else None
        )
        before = (
            f"{record.employee_id}: {record.starts_at} to {record.ends_at}; {record.notes}"
            if record
            else "No shift"
        )
        for shift_start, shift_end in intervals:
            action = "schedule_updated" if record else "schedule_created"
            shift = record or ScheduledShift()
            if record is None:
                db.session.add(shift)
            shift.employee_id, shift.starts_at, shift.ends_at, shift.notes = (
                employee.id,
                shift_start,
                shift_end,
                notes,
            )
            db.session.flush()
            create_audit_log(
                current_user,
                action,
                "ScheduledShift",
                str(shift.id),
                f"Before: {before}. After: {employee.id}: {shift_start} to {shift_end}; {notes} (UTC).",
            )
        if old_interval != (employee.id, start, end, notes):
            zone = workplace_zone()
            labels = [
                f"{local_datetime(first, zone).strftime('%a, %b %d %I:%M %p')} to {local_datetime(last, zone).strftime('%a, %b %d %I:%M %p')}"
                for first, last in intervals
            ]
            if employee.user:
                create_notification(
                    employee.user,
                    "Shift changed" if record else "New shifts posted",
                    "; ".join(labels) + f" ({zone}). Check your schedule.",
                    "/schedule?week=" + monday(local_datetime(start, zone).date()).isoformat(),
                    "schedule",
                )
            if old_employee and old_employee.id != employee.id and old_employee.user:
                create_notification(
                    old_employee.user,
                    "Shift reassigned",
                    f"Your shift starting {local_datetime(old_interval[1], zone).strftime('%a, %b %d %I:%M %p')} was reassigned. Check your schedule.",
                    "/schedule?week="
                    + monday(local_datetime(old_interval[1], zone).date()).isoformat(),
                    "schedule",
                )
        db.session.commit()
        flash(
            "Schedule saved." if len(intervals) == 1 else f"{len(intervals)} shifts saved.",
            "success",
        )
        saved_week = monday(local_datetime(start, workplace_zone()).date())
        return redirect(url_for("workplace.schedule", week=saved_week.isoformat()))
    if request.args.get("edit"):
        require_manager(write=True)
        record = db.get_or_404(ScheduledShift, request.args["edit"])
        if record.cancelled:
            abort(404)
    return render_template("schedule.html", **schedule_context(week, record))


@workplace_bp.post("/schedule/<int:shift_id>/cancel")
@login_required
def cancel_shift(shift_id):
    require_manager(write=True)
    staff_id = schedule_staff_id()
    try:
        week = selected_week(
            request.form.get("week"),
            request.args.get("days"),
            local_datetime(utcnow(), workplace_zone()).date(),
        )
    except ValueError as exc:
        abort(400, description=str(exc))
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
    if record.employee.user:
        create_notification(
            record.employee.user,
            "Shift cancelled",
            f"Your shift starting {local_datetime(record.starts_at, workplace_zone()).strftime('%a, %b %d %I:%M %p')} was cancelled.",
            "/schedule?week="
            + monday(local_datetime(record.starts_at, workplace_zone()).date()).isoformat(),
            "schedule",
        )
    db.session.commit()
    flash("Shift cancelled.", "success")
    return redirect(url_for("workplace.schedule", week=week.isoformat(), staff=staff_id))


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


def selected_channel():
    value = (
        request.args.get("channel_id", "1")
        if request.method == "GET"
        else request.form.get("channel_id", "1")
    )
    if request.is_json:
        payload = request.get_json(silent=True)
        if isinstance(payload, dict):
            value = str(payload.get("channel_id", "1"))
    if not value.isascii() or not value.isdigit() or len(value) > 18 or int(value) < 1:
        abort(400, description="Choose a chat channel.")
    return db.get_or_404(TeamChannel, int(value))


@workplace_bp.get("/chat")
@workplace_bp.get("/chat/<int:channel_id>")
@login_required
def chat(channel_id=1):
    require_coworker()
    channel = db.get_or_404(TeamChannel, channel_id)
    return render_template(
        "chat.html",
        active_page="chat",
        page_title="Team chat",
        channel=channel,
        channels=TeamChannel.query.order_by(TeamChannel.id).all(),
        messages=latest_messages(channel.id),
        coworkers=employees(),
    )


@workplace_bp.post("/chat/channels")
@login_required
@limiter.limit("10 per minute", key_func=lambda: str(current_user.id))
def create_channel():
    require_coworker()
    if current_user.role not in {Role.ADMIN, Role.EXECUTIVE}:
        abort(403)
    name = request.form.get("name", "").strip().lower().replace(" ", "-")
    topic = request.form.get("topic", "").strip()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", name):
        abort(400, description="Use 1 to 40 letters, numbers, or hyphens for the channel name.")
    if len(topic) > 160:
        abort(400, description="Keep the channel description under 160 characters.")
    if TeamChannel.query.filter_by(name=name).first():
        abort(409, description="A channel with that name already exists.")
    channel = TeamChannel(name=name, topic=topic, created_by_id=current_user.id)
    db.session.add(channel)
    db.session.flush()
    create_audit_log(
        current_user,
        "chat_channel_created",
        "TeamChannel",
        str(channel.id),
        f"Created #{name}. {topic}",
    )
    db.session.commit()
    flash("Channel created. Your coworkers can join the conversation.", "success")
    return redirect(url_for("workplace.chat", channel_id=channel.id))


def message_data(message):
    employee = message.user.employee
    attachment = message.attachment
    return dict(
        id=message.id,
        channel_id=message.channel_id,
        author=employee.full_name() if employee else message.user.username,
        body=message.body,
        created_at=message.created_at.replace(tzinfo=timezone.utc).isoformat(),
        attachment=dict(
            url=url_for("workplace.picture", attachment_id=attachment.id),
            filename=attachment.filename,
        )
        if attachment
        else None,
    )


@workplace_bp.get("/api/chat/messages")
@login_required
def messages():
    require_coworker()
    channel = selected_channel()
    after = request.args.get("after", "0")
    before = request.args.get("before", "0")
    for value in (after, before):
        if not value.isascii() or not value.isdigit() or len(value) > 18:
            abort(400, description="Invalid message cursor.")
    cursor, older = int(after), int(before)
    if cursor and older:
        abort(400, description="Choose one message cursor.")
    rows = (
        TeamMessage.query.filter(TeamMessage.channel_id == channel.id, TeamMessage.id > cursor)
        .order_by(TeamMessage.id)
        .limit(100)
        .all()
        if cursor
        else latest_messages(channel.id, older)
    )
    has_older = bool(
        rows
        and TeamMessage.query.filter(
            TeamMessage.channel_id == channel.id, TeamMessage.id < rows[0].id
        ).first()
    )
    return jsonify(messages=[message_data(row) for row in rows], has_older=has_older)


@workplace_bp.post("/api/chat/messages")
@login_required
@limiter.limit("30 per minute", key_func=lambda: str(current_user.id))
def send_message():
    require_coworker()
    channel = selected_channel()
    payload = request.get_json(silent=True) if request.is_json else request.form
    body = payload.get("body") if hasattr(payload, "get") else None
    upload = request.files.get("picture")
    if not isinstance(body, str) or len(body.strip()) > 2000 or (not body.strip() and not upload):
        abort(
            400,
            description="Write a message or attach a picture. Messages can be up to 2,000 characters.",
        )
    picture_data = None
    if upload:
        try:
            picture_data = normalize_picture(upload)
        except ValueError as exc:
            abort(400, description=str(exc))
    message = TeamMessage(user_id=current_user.id, channel_id=channel.id, body=body.strip())
    db.session.add(message)
    if picture_data:
        message.attachment = ChatAttachment(**picture_data)
    db.session.commit()
    return jsonify(message=message_data(message)), 201


@workplace_bp.get("/chat/pictures/<int:attachment_id>")
@login_required
def picture(attachment_id):
    require_coworker()
    attachment = db.get_or_404(ChatAttachment, attachment_id)
    return send_file(
        BytesIO(attachment.data),
        mimetype=attachment.mime_type,
        download_name=attachment.filename,
        max_age=0,
        conditional=False,
    )
