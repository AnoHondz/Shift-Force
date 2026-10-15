"""Preview actual punch intervals before preparing explicitly reviewed daily payroll."""

import hashlib
import json
import sqlite3
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy.exc import OperationalError
from werkzeug.exceptions import Conflict

from .extensions import db
from .models import Attendance, PayrollRecord, ScheduledShift
from .planning import seconds
from .scheduling import day_boundary


def serialize_command():
    """Acquire the SQLite writer before reading a command snapshot across workers."""
    connection = db.session.connection()
    if connection.dialect.name == "sqlite":
        # sqlite3 legacy transaction mode does not BEGIN for ordinary SELECTs.
        if not connection.connection.driver_connection.in_transaction:
            try:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            except OperationalError as exc:
                db.session.rollback()
                if getattr(exc.orig, "sqlite_errorcode", None) in {
                    sqlite3.SQLITE_BUSY,
                    sqlite3.SQLITE_LOCKED,
                }:
                    raise Conflict(
                        "The workplace is busy saving another change. Refresh and try again."
                    ) from exc
                raise
        db.session.expire_all()


def punch_rows(employee_id, week, zone):
    start, end = day_boundary(week, zone), day_boundary(week + timedelta(days=7), zone)
    return (
        Attendance.query.filter(
            Attendance.employee_id == employee_id,
            Attendance.clock_in_at < end,
            db.or_(Attendance.clock_out_at.is_(None), Attendance.clock_out_at > start),
        )
        .order_by(Attendance.id)
        .all()
    )


def punch_signature(employee_id, week, zone, rows=None):
    rows = rows if rows is not None else punch_rows(employee_id, week, zone)
    values = [
        (
            row.id,
            row.version_id,
            row.clock_in_at.isoformat(),
            row.clock_out_at.isoformat() if row.clock_out_at else None,
        )
        for row in rows
    ]
    return hashlib.sha256(
        json.dumps(
            [employee_id, week.isoformat(), str(zone), values], separators=(",", ":")
        ).encode()
    ).hexdigest()


def time_preview(employee, week, zone, today):
    punches = punch_rows(employee.id, week, zone)
    signature = punch_signature(employee.id, week, zone, punches)
    records = (
        PayrollRecord.query.filter(
            PayrollRecord.employee_id == employee.id,
            PayrollRecord.work_date.between(week, week + timedelta(days=6)),
        )
        .order_by(PayrollRecord.work_date)
        .all()
    )
    record_map = {record.work_date: record for record in records}
    start, end = day_boundary(week, zone), day_boundary(week + timedelta(days=7), zone)
    scheduled = ScheduledShift.query.filter(
        ScheduledShift.employee_id == employee.id,
        ScheduledShift.cancelled.is_(False),
        ScheduledShift.published.is_(True),
        ScheduledShift.starts_at < end,
        ScheduledShift.ends_at > start,
    ).all()
    token = hashlib.sha256(
        json.dumps(
            [
                signature,
                str(employee.hourly_rate),
                str(employee.overtime_multiplier),
                [
                    (
                        row.id,
                        row.version_id,
                        row.status,
                        str(row.hours_worked),
                        row.attendance_fingerprint,
                    )
                    for row in records
                ],
            ],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    issues, days = [], []
    if any(row.clock_out_at is None for row in punches):
        issues.append("There is an open punch. Fix its clock-out before preparing this week.")
    if employee.archived:
        issues.append("This employee is archived.")
    if any(row.legacy_import for row in records if row.status not in {"Voided", "Rejected"}):
        issues.append("Imported records require source reconciliation before preparing this week.")
    if any(row.status == "Approved" for row in records):
        issues.append(
            "Approved entries are protected. Make an authorized payroll correction before preparing this week."
        )
    if any(row.status == "Voided" for row in records):
        issues.append(
            "Voided entries need explicit manual correction; preparation will not revive them."
        )
    for offset in range(7):
        day = week + timedelta(days=offset)
        first, last = day_boundary(day, zone), day_boundary(day + timedelta(days=1), zone)
        elapsed = sum(
            (
                seconds(min(row.clock_out_at, last) - max(row.clock_in_at, first))
                for row in punches
                if row.clock_out_at and row.clock_in_at < last and row.clock_out_at > first
            ),
            Decimal(0),
        )
        planned = sum(
            (
                seconds(min(row.ends_at, last) - max(row.starts_at, first))
                for row in scheduled
                if row.starts_at < last and row.ends_at > first
            ),
            Decimal(0),
        )
        actual = (elapsed / Decimal(3600)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        planned_hours = (planned / Decimal(3600)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if actual > 24:
            issues.append(f"{day}: more than 24 daily hours needs manual review.")
        if actual and day > today:
            issues.append("A recorded punch is in the future. Correct it before preparing hours.")
        record = record_map.get(day)
        if (
            record
            and not record.attendance_fingerprint
            and record.status != "Voided"
            and record.hours_worked != actual
        ):
            issues.append(
                f"{day}: manual payroll differs from clock hours. Reconcile the payable work first; preparation will not replace it."
            )
        days.append(
            dict(
                date=day,
                actual=actual,
                planned=planned_hours,
                difference=actual - planned_hours,
                record=record,
                stale=bool(
                    record
                    and record.attendance_fingerprint
                    and record.attendance_fingerprint != signature
                ),
            )
        )
    if not any(item["actual"] for item in days):
        issues.append("No completed clocked hours were found for this week.")
    return dict(
        employee=employee,
        week=week,
        days=days,
        issues=issues,
        token=token,
        signature=signature,
        records=records,
        total=sum((item["actual"] for item in days), Decimal(0)),
    )


def source_stale_ids(records, zone):
    from .scheduling import monday

    cache, stale = {}, set()
    for record in records:
        if record.attendance_fingerprint:
            key = (record.employee_id, monday(record.work_date))
            if key not in cache:
                cache[key] = punch_signature(*key, zone)
            if cache[key] != record.attendance_fingerprint:
                stale.add(record.id)
    return stale
