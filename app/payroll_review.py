"""Production approval is limited to an explicitly reviewed Virginia hourly policy."""

from datetime import date
from decimal import Decimal

from flask import abort, current_app

from .models import OperationalReview, PayrollRecord
from .payroll import Workweek

POLICY_SCOPE = "Virginia; 2026-2028 work dates; adult hourly nonexempt; single-location work for this employer; Monday-Sunday workweek; weekly 40-hour overtime; at least 1.5 multiplier; no tips, bonuses, multiple-rate work, deductions or payment processing."


def minimum_wage(work_date):
    # Effective dates published by Virginia DOLI; review again when law changes.
    if not date(2026, 1, 1) <= work_date < date(2029, 1, 1):
        raise ValueError("The recorded Virginia policy supports work dates in 2026-2028 only.")
    if work_date >= date(2028, 1, 1):
        return Decimal("15.00")
    if work_date >= date(2027, 1, 1):
        return Decimal("13.75")
    return Decimal("12.77")


def require_payroll_approval(record):
    if record.attendance_fingerprint:
        from zoneinfo import ZoneInfo

        from .time_review import punch_signature

        current = punch_signature(
            record.employee_id,
            Workweek.containing(record.work_date).start,
            ZoneInfo(current_app.config.get("WORKFORCE_TIMEZONE", "UTC")),
        )
        if current != record.attendance_fingerprint:
            abort(
                409,
                description="Clock records changed after these hours were prepared. Reconcile clock hours before approval.",
            )
    if not current_app.config.get(
        "REQUIRE_PAYROLL_REVIEW", current_app.config.get("APP_ENV") == "production"
    ):
        return
    review = (
        OperationalReview.query.filter_by(review_type="virginia_payroll_policy", scope=POLICY_SCOPE)
        .order_by(OperationalReview.id.desc())
        .first()
    )
    if not review:
        abort(
            409,
            description="The store owner must complete Payroll readiness before approving wages.",
        )
    if current_app.config.get("WORKFORCE_TIMEZONE") != "America/New_York":
        abort(409, description="This reviewed policy requires the Virginia workplace time zone.")
    try:
        minimum = minimum_wage(record.work_date)
    except ValueError as exc:
        abort(409, description=str(exc))
    if record.rate_snapshot < minimum or record.multiplier_snapshot < Decimal("1.5"):
        abort(
            409,
            description="This entry falls outside the reviewed Virginia wage policy. Review its compensation before approval.",
        )
    week = Workweek.containing(record.work_date)
    week_records = PayrollRecord.query.filter(
        PayrollRecord.employee_id == record.employee_id,
        PayrollRecord.work_date.between(week.start, week.end),
        PayrollRecord.status.notin_(["Rejected", "Voided"]),
    ).all()
    if any(row.legacy_import for row in week_records):
        abort(
            409,
            description="This workweek contains imported records. Reconcile it against the source timesheets first.",
        )
    if len({(row.rate_snapshot, row.multiplier_snapshot) for row in week_records}) > 1:
        abort(
            409,
            description="This workweek has multiple compensation rates. Have your payroll professional review its regular rate and overtime outside this supported policy.",
        )
