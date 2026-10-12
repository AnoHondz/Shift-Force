"""Explicit Monday–Sunday workweeks and inclusive reporting pay periods."""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from .utils import decimal_value


@dataclass(frozen=True)
class Workweek:
    start: date

    @classmethod
    def containing(cls, work_date):
        return cls(work_date - timedelta(days=work_date.weekday()))

    @property
    def end(self):
        return self.start + timedelta(days=6)


@dataclass(frozen=True)
class PayPeriod:
    start: date
    end: date

    def __post_init__(self):
        if self.end < self.start:
            raise ValueError("Pay period end must be on or after its start.")


def calculate_pay(hours, rate, multiplier, prior_hours=0):
    """Allocate a daily total after earlier shifts in the same workweek."""
    hours = decimal_value(hours, maximum=24)
    rate = decimal_value(rate)
    multiplier = decimal_value(multiplier, minimum=1, maximum=5)
    prior = decimal_value(prior_hours, maximum=168)
    regular = min(hours, max(Decimal("40") - prior, Decimal("0")))
    overtime = hours - regular
    total = (regular * rate + overtime * rate * multiplier).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return regular, overtime, total
