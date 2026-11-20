"""Build contractual cash flows for fixed-rate deals."""

from __future__ import annotations

from calendar import isleap, monthrange
from dataclasses import dataclass
from datetime import date
import math


DAY_COUNTS = {"ACT/365": 365, "ACT/360": 360, "ACT/ACT ISDA": None}
PAYMENT_TYPES = ("ANN", "RGM", "SIMPLE")


def finite_number(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{field}: expected a finite number")
    return float(value)


def parse_date(value: str) -> date:
    if not isinstance(value, str):
        raise ValueError("Expected a date in YYYY-MM-DD format")
    result = date.fromisoformat(value)
    if value != result.isoformat():
        raise ValueError("Expected a date in YYYY-MM-DD format")
    return result


def monthly_dates(start: date, term_months: int, interval_months: int) -> tuple[date, ...]:
    for name, value in (("term_months", term_months), ("payment_interval_months", interval_months)):
        if type(value) is not int or value <= 0:
            raise ValueError(f"{name}: expected a positive integer")
    if (start.year * 12 + start.month - 1 + term_months) // 12 > date.max.year:
        raise ValueError("Maturity is outside the supported date range")
    # Include maturity even when the final period is shorter than the payment interval.
    offsets = [*range(interval_months, term_months, interval_months), term_months]
    result = []
    for offset in offsets:
        year, month = divmod(start.year * 12 + start.month - 1 + offset, 12)
        month += 1
        result.append(date(year, month, min(start.day, monthrange(year, month)[1])))
    return tuple(result)


@dataclass(frozen=True)
class Deal:
    deal_id: str
    principal: float
    currency: str
    start_date: date
    payment_dates: tuple[date, ...]
    payment_type: str
    day_count: str
    annual_rate: float
    rate_type: str
    source: str

    def __post_init__(self) -> None:
        for field in ("deal_id", "currency", "source"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field}: expected non-empty text")
        if self.rate_type != "FIXED":
            raise ValueError("Only FIXED deals are supported. Floating-rate reset rules are required.")
        if self.payment_type not in PAYMENT_TYPES:
            raise ValueError(f"Unknown payment type: {self.payment_type}")
        if self.day_count not in DAY_COUNTS:
            raise ValueError(f"Unknown day count: {self.day_count}")
        if finite_number(self.principal, "principal") <= 0:
            raise ValueError("Principal must be positive")
        if finite_number(self.annual_rate, "annual_rate") < 0:
            raise ValueError("Annual rate must be a nonnegative fraction")
        if not self.payment_dates or any(
            end <= start
            for start, end in zip((self.start_date, *self.payment_dates), self.payment_dates)
        ):
            raise ValueError("Payment dates must be strictly increasing and after start_date")

    @classmethod
    def from_dict(cls, data: dict) -> Deal:
        start = parse_date(data["start_date"])
        if "payment_dates" in data:
            if "term_months" in data or "payment_interval_months" in data:
                raise ValueError("Supply payment_dates or term_months and payment_interval_months")
            if not isinstance(data["payment_dates"], list):
                raise ValueError("payment_dates must be a list")
            dates = tuple(map(parse_date, data["payment_dates"]))
        else:
            dates = monthly_dates(start, data["term_months"], data["payment_interval_months"])
        return cls(
            deal_id=data["deal_id"],
            principal=data["principal"],
            currency=data["currency"],
            start_date=start,
            payment_dates=dates,
            payment_type=data["payment_type"],
            day_count=data["day_count"],
            annual_rate=data["annual_rate"],
            rate_type=data["rate_type"],
            source=data["source"],
        )

    def to_dict(self) -> dict:
        return {
            "deal_id": self.deal_id,
            "principal": self.principal,
            "currency": self.currency,
            "start_date": self.start_date.isoformat(),
            "payment_dates": [day.isoformat() for day in self.payment_dates],
            "payment_type": self.payment_type,
            "day_count": self.day_count,
            "annual_rate": self.annual_rate,
            "rate_type": self.rate_type,
            "source": self.source,
        }


def _day_count_terms(start: date, end: date, convention: str) -> dict[int, int]:
    """Return actual days grouped by the denominator used to accrue interest."""
    denominator = DAY_COUNTS[convention]
    if denominator is not None:
        return {denominator: (end - start).days}

    terms = {}
    while start < end:
        # Split at calendar year boundaries, including start and excluding end.
        boundary = date(start.year + 1, 1, 1) if start.year < end.year else end
        denominator = 366 if isleap(start.year) else 365
        terms[denominator] = terms.get(denominator, 0) + (boundary - start).days
        start = boundary
    return terms


def build_schedule(deal: Deal) -> list[dict]:
    starts = (deal.start_date, *deal.payment_dates[:-1])
    terms = [_day_count_terms(start, end, deal.day_count) for start, end in zip(starts, deal.payment_dates)]
    fractions = [math.fsum(count / denominator for denominator, count in group.items()) for group in terms]
    period_rates = [deal.annual_rate * fraction for fraction in fractions]
    if not all(map(math.isfinite, period_rates)):
        raise ValueError("Period rates exceed the supported numerical range")

    if deal.payment_type == "ANN":
        # Equivalent to the sum of cumulative discount factors at the contractual rates.
        annuity_factor = 0.0
        for rate in reversed(period_rates):
            annuity_factor = (1 + annuity_factor) / (1 + rate)
        regular_payment = deal.principal / annuity_factor

    schedule = []
    closing = deal.principal
    for index, (start, end, group, fraction, rate) in enumerate(
        zip(starts, deal.payment_dates, terms, fractions, period_rates), 1
    ):
        opening = closing
        interest = opening * rate
        if index == len(terms):
            principal = opening
        elif deal.payment_type == "ANN":
            principal = regular_payment - interest
            if not 0 <= principal <= opening:
                raise ValueError(f"Payment {index}: annuity would increase or overpay the principal")
        elif deal.payment_type == "RGM":
            principal = deal.principal / len(terms)
        else:
            principal = 0.0
        closing = opening - principal
        payment = principal + interest
        if not math.isfinite(payment):
            raise ValueError(f"Payment {index}: amount exceeds the supported numerical range")
        schedule.append({
            "number": index,
            "period_start": start.isoformat(),
            "payment_date": end.isoformat(),
            "days": sum(group.values()),
            "day_count_terms": group,
            "year_fraction": fraction,
            "opening_balance": opening,
            "interest": interest,
            "principal": principal,
            "payment": payment,
            "closing_balance": closing,
        })
    return schedule
