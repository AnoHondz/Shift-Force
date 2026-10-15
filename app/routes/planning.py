"""Small self-service and manager commands over store-scoped scheduling rules."""

import json
from datetime import timedelta

from flask import Blueprint, Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..extensions import db, limiter
from ..models import (
    AvailabilityProfile,
    Role,
    ScheduledShift,
    ShiftCoverage,
    User,
    create_audit_log,
    create_notification,
    utcnow,
)
from ..planning import availability_rules, parse_availability, roster_signature, shift_notice
from ..scheduling import day_boundary, local_datetime, selected_week, validate_availability
from ..time_review import serialize_command
from ..utils import parse_positive_int
from .workplace import (
    parse_shift_time,
    require_coworker,
    require_manager,
    schedule_staff_id,
    validate_interval,
    verify_version,
    workplace_zone,
)

planning_bp = Blueprint("planning", __name__)


def action_key():
    return current_user.get_id() or "anonymous"


def week_value():
    try:
        return selected_week(
            request.values.get("week"), None, local_datetime(utcnow(), workplace_zone()).date()
        )
    except ValueError as exc:
        abort(400, description=str(exc))


def week_shifts(week, staff_id=None, started=False):
    start, end = (
        day_boundary(week, workplace_zone()),
        day_boundary(week + timedelta(days=7), workplace_zone()),
    )
    query = ScheduledShift.query.filter(
        ScheduledShift.cancelled.is_(False), ScheduledShift.starts_at < end
    )
    query = query.filter(
        ScheduledShift.starts_at >= start if started else ScheduledShift.ends_at > start
    )
    if staff_id:
        query = query.filter_by(employee_id=staff_id)
    return query.order_by(ScheduledShift.id).all()


@planning_bp.route("/schedule/availability", methods=("GET", "POST"))
@login_required
@limiter.limit("10 per minute", key_func=action_key, methods=["POST"])
def availability():
    employee = require_coworker()
    profile = AvailabilityProfile.query.filter_by(employee_id=employee.id).first()
    error = None
    rules = availability_rules(profile)
    if request.method == "POST":
        if request.form.get("version_id") != str(profile.version_id if profile else 0):
            abort(409, description="Availability changed. Refresh before saving.")
        try:
            rules = parse_availability(request.form)
        except ValueError as exc:
            error = str(exc)
        if error is None:
            encoded = json.dumps(rules, separators=(",", ":"))
            if profile and profile.rules == encoded:
                flash("Your availability is already up to date.", "success")
                return redirect(url_for("planning.availability"))
            if profile is None:
                profile = AvailabilityProfile(employee_id=employee.id)
                db.session.add(profile)
            profile.rules = encoded
            create_audit_log(
                current_user,
                "availability_updated",
                "Employee",
                str(employee.id),
                "Recurring weekly availability updated by the employee.",
            )
            for manager in User.query.filter(User.role.in_(Role.LEAVE_REVIEW)).all():
                if manager.id != current_user.id:
                    create_notification(
                        manager,
                        "Availability updated",
                        f"{employee.full_name()} updated their recurring availability. Existing shifts remain scheduled; review any conflicts.",
                        "/schedule",
                        "schedule",
                    )
            db.session.commit()
            flash("Availability saved. Your existing shifts have not been cancelled.", "success")
            return redirect(url_for("planning.availability"))
    return render_template(
        "availability.html",
        rules=rules,
        version=profile.version_id if profile else 0,
        error=error,
        timezone_name=str(workplace_zone()),
        active_page="schedule",
        page_title="My availability",
    ), 400 if error else 200


@planning_bp.post("/schedule/publish")
@login_required
@limiter.limit("10 per minute", key_func=action_key)
def publish():
    require_manager(write=True)
    serialize_command()
    week, staff_id = week_value(), schedule_staff_id()
    drafts = [row for row in week_shifts(week, staff_id) if not row.published]
    if request.form.get("draft_token") != roster_signature(drafts):
        abort(409, description="Drafts changed. Refresh and review before publishing.")
    if not drafts:
        abort(409, description="There are no drafts to publish.")
    try:
        for row in drafts:
            if row.employee.archived:
                raise ValueError(
                    "A draft belongs to an archived employee. Edit or cancel it first."
                )
            validate_availability(
                row.employee_id, row.starts_at, row.ends_at, workplace_zone(), exclude_id=row.id
            )
    except ValueError as exc:
        abort(409, description=str(exc))
    by_employee = {}
    for row in drafts:
        row.published = True
        by_employee.setdefault(row.employee_id, row.employee)
        create_audit_log(
            current_user,
            "schedule_published",
            "ScheduledShift",
            str(row.id),
            f"Published planned shift {row.starts_at} to {row.ends_at} (UTC).",
        )
    for employee in by_employee.values():
        if employee.user:
            create_notification(
                employee.user,
                "Schedule published",
                "Your shifts are ready. Open your schedule to see the hours.",
                "/schedule?week=" + week.isoformat(),
                "schedule",
            )
    db.session.commit()
    flash(f"Published {len(drafts)} shifts.", "success")
    return redirect(url_for("workplace.schedule", week=week.isoformat(), staff=staff_id))


@planning_bp.post("/schedule/copy")
@login_required
@limiter.limit("5 per minute", key_func=action_key)
def copy_week():
    require_manager(write=True)
    serialize_command()
    week, staff_id = week_value(), schedule_staff_id()
    source = week_shifts(week - timedelta(days=7), staff_id, started=True)
    target = week_shifts(week, staff_id)
    token = roster_signature(source) + ":" + roster_signature(target)
    if request.form.get("copy_token") != token:
        abort(409, description="The source or target schedule changed. Refresh before copying.")
    if not source or len(source) > 1000:
        abort(400, description="Choose a previous week with 1–1,000 shifts to copy.")
    intervals = []
    try:
        for row in source:
            if row.employee.archived:
                raise ValueError(
                    "The previous week includes an archived employee. Filter to active staff before copying."
                )
            local_start = local_datetime(row.starts_at, workplace_zone()).replace(
                tzinfo=None
            ) + timedelta(days=7)
            local_end = local_datetime(row.ends_at, workplace_zone()).replace(
                tzinfo=None
            ) + timedelta(days=7)
            start, end = (
                parse_shift_time(local_start.isoformat()),
                parse_shift_time(local_end.isoformat()),
            )
            validate_interval(start, end)
            validate_availability(row.employee_id, start, end, workplace_zone())
            if any(
                person == row.employee_id and first < end and last > start
                for person, first, last, _ in intervals
            ):
                raise ValueError("Copied shifts would overlap. Review the source week.")
            intervals.append((row.employee_id, start, end, row.notes))
    except ValueError as exc:
        abort(409, description=str(exc))
    for employee_id, start, end, notes in intervals:
        row = ScheduledShift(
            employee_id=employee_id, starts_at=start, ends_at=end, notes=notes, published=False
        )
        db.session.add(row)
        db.session.flush()
        create_audit_log(
            current_user,
            "schedule_copied",
            "ScheduledShift",
            str(row.id),
            f"Copied as private draft from week {(week - timedelta(days=7)).isoformat()}.",
        )
    db.session.commit()
    flash(f"Copied {len(intervals)} shifts as drafts. Review and publish when ready.", "success")
    return redirect(url_for("workplace.schedule", week=week.isoformat(), staff=staff_id))


def active_offer(offer):
    shift = offer.shift
    if (
        offer.status not in {"Open", "Pending"}
        or shift.cancelled
        or not shift.published
        or shift.starts_at <= utcnow()
        or shift.employee_id != offer.offered_by_id
        or shift.version_id != offer.shift_version
        or offer.offered_by.archived
    ):
        abort(409, description="This coverage offer changed or expired. Refresh the schedule.")
    return shift


@planning_bp.get("/schedule/coverage")
@login_required
def coverage():
    employee = require_coworker()
    pagination = (
        ShiftCoverage.query.join(ScheduledShift)
        .filter(
            ShiftCoverage.status.in_(["Open", "Pending"]),
            ScheduledShift.published.is_(True),
            ScheduledShift.cancelled.is_(False),
            ScheduledShift.starts_at > utcnow(),
            ShiftCoverage.offered_by.has(archived=False),
        )
        .order_by(ScheduledShift.starts_at)
        .paginate(
            page=parse_positive_int(request.args.get("page"), default=1),
            per_page=30,
            error_out=False,
        )
    )
    return render_template(
        "coverage.html",
        offers=pagination.items,
        pagination=pagination,
        employee=employee,
        can_review=current_user.role in Role.LEAVE_REVIEW,
        active_page="schedule",
        page_title="Shift coverage",
    )


@planning_bp.post("/schedule/<int:shift_id>/offer")
@login_required
@limiter.limit("10 per minute", key_func=action_key)
def offer_shift(shift_id):
    serialize_command()
    employee = require_coworker()
    shift = db.get_or_404(ScheduledShift, shift_id)
    if shift.employee_id != employee.id:
        abort(403)
    verify_version(shift)
    if not shift.published or shift.cancelled or shift.starts_at <= utcnow():
        abort(409, description="Only your upcoming published shifts can be offered.")
    note = request.form.get("note", "").strip()
    if len(note) > 255:
        abort(400, description="Keep the coverage note within 255 characters.")
    offer = ShiftCoverage(
        shift=shift, offered_by_id=employee.id, shift_version=shift.version_id, note=note
    )
    db.session.add(offer)
    db.session.flush()
    create_audit_log(
        current_user,
        "coverage_offered",
        "ShiftCoverage",
        str(offer.id),
        f"Coverage offered for shift {shift.id}; original employee remains responsible until approved.",
    )
    db.session.commit()
    flash("Shift offered. It is still yours until a manager approves coverage.", "success")
    return redirect(url_for("planning.coverage"))


@planning_bp.post("/schedule/coverage/<int:offer_id>/claim")
@login_required
@limiter.limit("20 per minute", key_func=action_key)
def claim(offer_id):
    serialize_command()
    employee = require_coworker()
    offer = db.get_or_404(ShiftCoverage, offer_id)
    verify_version(offer)
    shift = active_offer(offer)
    if employee.id == offer.offered_by_id:
        abort(403)
    if offer.status != "Open":
        abort(409, description="Another coverage request is awaiting review.")
    try:
        validate_availability(employee.id, shift.starts_at, shift.ends_at, workplace_zone())
    except ValueError as exc:
        abort(409, description=str(exc))
    offer.claimed_by_id, offer.status = employee.id, "Pending"
    for manager in User.query.filter(User.role.in_(Role.LEAVE_REVIEW)).all():
        create_notification(
            manager,
            "Coverage needs approval",
            f"{employee.full_name()} requested an offered shift. Review availability before accepting.",
            "/schedule/coverage",
            "approval",
        )
    create_audit_log(
        current_user,
        "coverage_claimed",
        "ShiftCoverage",
        str(offer.id),
        "Coworker requested coverage; no assignment changed.",
    )
    db.session.commit()
    flash("Request sent. Wait for manager approval before treating this as your shift.", "success")
    return redirect(url_for("planning.coverage"))


@planning_bp.post("/schedule/coverage/<int:offer_id>/review")
@login_required
@limiter.limit("30 per minute", key_func=action_key)
def review(offer_id):
    require_manager(write=True)
    serialize_command()
    offer = db.get_or_404(ShiftCoverage, offer_id)
    verify_version(offer)
    shift = active_offer(offer)
    if offer.status != "Pending" or not offer.claimed_by:
        abort(409, description="There is no active coworker request to review.")
    if current_user.employee_id in {offer.offered_by_id, offer.claimed_by_id}:
        abort(403, description="Another manager must review coverage involving your shift.")
    action = request.form.get("action")
    if action not in {"approve", "reject"}:
        abort(400)
    claimant = offer.claimed_by
    if action == "approve":
        if claimant.archived:
            abort(
                409,
                description="This coworker is archived. Decline the request or choose another coworker.",
            )
        try:
            validate_availability(claimant.id, shift.starts_at, shift.ends_at, workplace_zone())
        except ValueError as exc:
            abort(409, description=str(exc))
        shift.employee_id, offer.status = claimant.id, "Approved"
        shift_notice(offer.offered_by, "Coverage approved", shift, workplace_zone())
        shift_notice(claimant, "Your coverage is approved", shift, workplace_zone())
        create_audit_log(
            current_user,
            "schedule_updated",
            "ScheduledShift",
            str(shift.id),
            f"Approved coverage: employee {offer.offered_by_id} to {claimant.id}.",
        )
    else:
        offer.status, offer.claimed_by_id = "Open", None
        if claimant.user:
            create_notification(
                claimant.user,
                "Coverage request declined",
                "The shift remains with its original employee.",
                "/schedule/coverage",
                "schedule",
            )
    offer.reviewed_by_id = current_user.id
    create_audit_log(
        current_user,
        "coverage_reviewed",
        "ShiftCoverage",
        str(offer.id),
        f"Coverage {action} for shift {shift.id}.",
    )
    db.session.commit()
    flash(
        "Coverage approved."
        if action == "approve"
        else "Request declined. The offer is open again.",
        "success",
    )
    return redirect(url_for("planning.coverage"))


@planning_bp.post("/schedule/coverage/<int:offer_id>/cancel")
@login_required
@limiter.limit("20 per minute", key_func=action_key)
def cancel_offer(offer_id):
    serialize_command()
    employee = require_coworker()
    offer = db.get_or_404(ShiftCoverage, offer_id)
    if offer.offered_by_id != employee.id:
        abort(403)
    verify_version(offer)
    if offer.status not in {"Open", "Pending"}:
        abort(409)
    offer.status = "Cancelled"
    if offer.claimed_by and offer.claimed_by.user:
        create_notification(
            offer.claimed_by.user,
            "Coverage offer withdrawn",
            "The original employee withdrew the offer.",
            "/schedule/coverage",
            "schedule",
        )
    create_audit_log(
        current_user,
        "coverage_cancelled",
        "ShiftCoverage",
        str(offer.id),
        "Original employee withdrew coverage; shift retained.",
    )
    db.session.commit()
    return redirect(url_for("planning.coverage"))


def calendar_text(value):
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("\r", "")
        .replace("\n", "\\n")
        .replace(";", "\\;")
        .replace(",", "\\,")
    )


def folded_line(value):
    parts, current = [], ""
    for character in value:
        if len((current + character).encode("utf-8")) > 74:
            parts.append(current)
            current = " "
        current += character
    parts.append(current)
    return "\r\n".join(parts)


@planning_bp.get("/schedule/calendar.ics")
@login_required
def calendar():
    from flask import current_app

    employee, week = require_coworker(), week_value()
    shifts = [row for row in week_shifts(week, employee.id) if row.published]
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    scope = current_app.config.get("STORE_SLUG") or "hub"
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Shiftspace//Personal schedule//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    for shift in shifts:
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{scope}-{shift.id}@shiftspace",
                f"SEQUENCE:{shift.version_id}",
                "DTSTAMP:" + stamp,
                "DTSTART:" + shift.starts_at.strftime("%Y%m%dT%H%M%SZ"),
                "DTEND:" + shift.ends_at.strftime("%Y%m%dT%H%M%SZ"),
                "SUMMARY:Shift",
                "DESCRIPTION:" + calendar_text(shift.notes),
                "END:VEVENT",
            ]
        )
    lines.append("END:VCALENDAR")
    response = Response(
        "\r\n".join(folded_line(line) for line in lines) + "\r\n",
        content_type="text/calendar; charset=utf-8",
    )
    response.headers["Content-Disposition"] = (
        f'attachment; filename="shiftspace-{week.isoformat()}.ics"'
    )
    response.headers["Cache-Control"] = "no-store"
    return response
