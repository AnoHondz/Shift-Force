"""Local calendar planning over UTC shifts; no payroll or inferred availability."""

import re
from datetime import date, datetime, time, timedelta, timezone

from .models import LeaveRequest, ScheduledShift


def local_datetime(value, zone):
    return value.replace(tzinfo=timezone.utc).astimezone(zone)


def monday(value):
    return value - timedelta(days=value.weekday())


def selected_week(value, days, today):
    if value:
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
            raise ValueError("Choose a valid schedule week.")
        chosen = date.fromisoformat(value)
    else:
        try:
            offset = int(days or 0)
        except ValueError:
            raise ValueError("Choose a valid schedule week.") from None
        if abs(offset) > 3650:
            raise ValueError("Choose a week within ten years of today.")
        chosen = today + timedelta(days=offset)
    if abs((chosen - today).days) > 3650:
        raise ValueError("Choose a week within ten years of today.")
    return monday(chosen)


def day_boundary(day, zone):
    return datetime.combine(day, time.min, zone).astimezone(timezone.utc).replace(tzinfo=None)


def repeat_intervals(start, end, values, zone, parse_time, editing=False):
    if not values:
        return [(start, end)]
    if editing:
        raise ValueError("Edit one shift at a time. Add a new shift to repeat hours.")
    if len(values) > 7 or any(value not in tuple(str(i) for i in range(7)) for value in values):
        raise ValueError("Choose valid days to repeat this shift.")
    local_start, local_end = local_datetime(start, zone), local_datetime(end, zone)
    intervals = [(start, end)]
    for weekday in sorted({int(value) for value in values} - {local_start.weekday()}):
        delta = timedelta(days=weekday - local_start.weekday())
        repeated_start = (local_start.replace(tzinfo=None) + delta).isoformat()
        repeated_end = (local_end.replace(tzinfo=None) + delta).isoformat()
        intervals.append((parse_time(repeated_start), parse_time(repeated_end)))
    # Sorting makes creation and audits deterministic, including days before the original.
    return sorted(intervals)


def validate_availability(employee_id, start, end, zone, exclude_id=None):
    overlap = ScheduledShift.query.filter(
        ScheduledShift.employee_id == employee_id,
        ScheduledShift.cancelled.is_(False),
        ScheduledShift.starts_at < end,
        ScheduledShift.ends_at > start,
    )
    if exclude_id is not None:
        overlap = overlap.filter(ScheduledShift.id != exclude_id)
    if overlap.first():
        day = local_datetime(start, zone).strftime("%a, %b %d")
        raise ValueError(f"This employee already has a shift during those hours on {day}.")
    first_day = local_datetime(start, zone).date()
    last_day = local_datetime(end - timedelta(microseconds=1), zone).date()
    leave = LeaveRequest.query.filter(
        LeaveRequest.employee_id == employee_id,
        LeaveRequest.status == "Approved",
        LeaveRequest.start_date <= last_day,
        LeaveRequest.end_date >= first_day,
    ).first()
    if leave:
        raise ValueError(
            "This employee has approved time off during this shift. Choose another day."
        )


def week_calendar(shifts, leaves, week, today, zone):
    calendar = []
    total_seconds = 0
    for offset in range(7):
        day = week + timedelta(days=offset)
        start, end = day_boundary(day, zone), day_boundary(day + timedelta(days=1), zone)
        cards = []
        seconds = 0
        day_leaves = [item for item in leaves if item.start_date <= day <= item.end_date]
        leave_ids = {item.employee_id for item in day_leaves}
        for shift in shifts:
            if shift.starts_at >= end or shift.ends_at <= start:
                continue
            hours = (min(end, shift.ends_at) - max(start, shift.starts_at)).total_seconds() / 3600
            seconds += hours * 3600
            local_start = local_datetime(shift.starts_at, zone)
            local_end = local_datetime(shift.ends_at, zone)
            cards.append(
                dict(
                    shift=shift,
                    start_label=local_start.strftime("%I:%M %p").lstrip("0"),
                    end_label=local_end.strftime("%I:%M %p").lstrip("0"),
                    continues=local_start.date() < day,
                    overnight=local_end.date() > local_start.date(),
                    end_day=local_end.strftime("%a"),
                    hours=hours,
                    leave_conflict=shift.employee_id in leave_ids,
                )
            )
        total_seconds += seconds
        calendar.append(
            dict(
                date=day,
                today=day == today,
                shifts=cards,
                hours=seconds / 3600,
                leaves=day_leaves,
            )
        )
    return calendar, total_seconds / 3600
