"""Store-local planning rules, availability and explicit roster snapshots."""

import hashlib
import json
import re
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from .models import AvailabilityProfile, ShiftCoverage, create_notification
from .scheduling import day_boundary, local_datetime, monday


def minutes(value, end=False):
    if not re.fullmatch(r"[0-9]{2}:[0-9]{2}", value):
        raise ValueError("Choose valid availability times.")
    hour, minute = map(int, value.split(":"))
    if hour > 23 or minute > 59:
        raise ValueError("Choose valid availability times.")
    number = hour * 60 + minute
    return 1440 if end and number == 0 else number


def parse_availability(values):
    result = []
    for day in range(7):
        mode = values.get(f"mode_{day}")
        if mode not in {"any", "off", "hours"}:
            raise ValueError("Choose an availability option for every day.")
        start, end = 0, 1440
        if mode == "hours":
            start = minutes(values.get(f"start_{day}", ""))
            end = minutes(values.get(f"end_{day}", ""), end=True)
            if end <= start:
                raise ValueError(
                    "Available hours must end after they start. Set overnight hours on both days."
                )
        result.append(dict(mode=mode, start=start, end=end))
    return result


def availability_rules(profile):
    if profile is None:
        return [dict(mode="any", start=0, end=1440) for _ in range(7)]
    rules = json.loads(profile.rules)
    if len(rules) != 7 or any(
        rule.get("mode") not in {"any", "off", "hours"}
        or not 0 <= rule.get("start", -1) < rule.get("end", -1) <= 1440
        for rule in rules
    ):
        raise ValueError("Stored availability needs review before scheduling.")
    return rules


def availability_conflict(employee_id, start, end, zone, profile=None):
    profile = profile or AvailabilityProfile.query.filter_by(employee_id=employee_id).first()
    if profile is None:
        return None  # No submission is unknown availability, not a fabricated restriction.
    rules = availability_rules(profile)
    day = local_datetime(start, zone).date()
    while day_boundary(day, zone) < end:
        first, last = day_boundary(day, zone), day_boundary(day + timedelta(days=1), zone)
        local_start = local_datetime(max(first, start), zone)
        local_end = local_datetime(min(last, end), zone)
        start_minute = local_start.hour * 60 + local_start.minute + local_start.second / 60
        end_minute = (
            1440
            if min(last, end) == last
            else local_end.hour * 60 + local_end.minute + local_end.second / 60
        )
        rule = rules[day.weekday()]
        if rule["mode"] == "off" or (
            rule["mode"] == "hours" and (start_minute < rule["start"] or end_minute > rule["end"])
        ):
            return f"Outside submitted availability on {day.strftime('%a, %b %d')}."
        day += timedelta(days=1)
    return None


def roster_signature(shifts):
    values = [
        (
            row.id,
            row.version_id,
            row.employee_id,
            row.starts_at.isoformat(),
            row.ends_at.isoformat(),
            row.notes,
            row.published,
            row.cancelled,
        )
        for row in shifts
    ]
    return hashlib.sha256(json.dumps(sorted(values), separators=(",", ":")).encode()).hexdigest()


def seconds(value):
    return Decimal(value.days * 86400 + value.seconds) + Decimal(value.microseconds) / Decimal(
        1000000
    )


def labor_projection(shifts, week, zone):
    start, end = day_boundary(week, zone), day_boundary(week + timedelta(days=7), zone)
    people = {}
    for row in shifts:
        if row.cancelled or row.ends_at <= start or row.starts_at >= end:
            continue
        item = people.setdefault(row.employee_id, dict(employee=row.employee, hours=Decimal(0)))
        item["hours"] += seconds(min(end, row.ends_at) - max(start, row.starts_at)) / Decimal(3600)
    cost = Decimal(0)
    overtime = []
    for item in people.values():
        employee, hours = item["employee"], item["hours"]
        extra = max(hours - 40, Decimal(0))
        cost += min(hours, Decimal(40)) * Decimal(str(employee.hourly_rate)) + extra * Decimal(
            str(employee.hourly_rate)
        ) * Decimal(str(employee.overtime_multiplier))
        if extra:
            overtime.append(dict(name=employee.full_name(), hours=extra))
    return dict(cost=cost.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), overtime=overtime)


def close_coverage(shift, reason):
    for offer in ShiftCoverage.query.filter(
        ShiftCoverage.shift_id == shift.id, ShiftCoverage.status.in_(["Open", "Pending"])
    ).all():
        offer.status = "Cancelled"
        if offer.claimed_by and offer.claimed_by.user:
            create_notification(
                offer.claimed_by.user,
                "Coverage request closed",
                reason,
                "/schedule/coverage",
                "schedule",
            )


def shift_notice(employee, title, shift, zone):
    if employee.user:
        local = local_datetime(shift.starts_at, zone)
        create_notification(
            employee.user,
            title,
            f"{local.strftime('%a, %b %d %I:%M %p')} ({zone}). Check your schedule.",
            "/schedule?week=" + monday(local.date()).isoformat(),
            "schedule",
        )
