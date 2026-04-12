from datetime import date

from flask_login import current_user
from sqlalchemy.orm import joinedload

from .models import Employee


def get_current_employee():
    if current_user.is_authenticated and current_user.employee_id:
        return Employee.query.options(joinedload(Employee.department)).get(current_user.employee_id)
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
    parsed = max(parsed, minimum)
    parsed = min(parsed, maximum)
    return parsed
