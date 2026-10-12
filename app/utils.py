import re
from datetime import date
from decimal import Decimal, InvalidOperation

from flask_login import current_user


def get_current_employee():
    from .models import Employee

    if current_user.is_authenticated and current_user.employee_id:
        from .extensions import db

        return db.session.get(Employee, current_user.employee_id)
    return None


def parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def parse_positive_int(value, default=1, minimum=1, maximum=500):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < minimum:
        return default
    parsed = min(parsed, maximum)
    return parsed


def valid_email(value):
    return len(value) <= 160 and bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value))


def valid_password(value):
    return 12 <= len(value) <= 128 and not value.isspace()


def decimal_value(value, minimum=0, maximum=10000):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Enter a valid number.") from None
    if not result.is_finite() or result < minimum or result > maximum:
        raise ValueError(f"Value must be between {minimum} and {maximum}.")
    if result != result.quantize(Decimal("0.01")):
        raise ValueError("Use at most two decimal places.")
    return result


def validate_employee_form(form, compensation=True):
    limits = {
        "first_name": 120,
        "last_name": 120,
        "job_title": 120,
        "phone": 40,
        "address": 255,
        "schedule": 64,
    }
    for field, maximum in limits.items():
        value = form.get(field, "").strip()
        if len(value) > maximum:
            raise ValueError(
                f"{field.replace('_', ' ').title()} must be at most {maximum} characters."
            )
    if not valid_email(form.get("email", "").strip()):
        raise ValueError("Enter a valid email address.")
    if compensation:
        decimal_value(form.get("hourly_rate", "0"))
        decimal_value(form.get("overtime_multiplier", "1.5"), minimum=1, maximum=5)
        department = form.get("department_id")
        if department:
            from .extensions import db
            from .models import Department

            if not department.isdigit() or db.session.get(Department, int(department)) is None:
                raise ValueError("Choose an existing department.")
