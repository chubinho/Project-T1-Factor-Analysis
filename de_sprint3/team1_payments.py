"""Load the daily payment schedule produced by the Team 1 event importer."""

from collections import defaultdict
import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .team1_events import CASHFLOW_TYPES, PAYMENT_FIELDS


@dataclass(frozen=True)
class Team1Schedule:
    deal_id: str
    currency: str
    source: str
    demo: bool
    rows: tuple[dict, ...]

    def describe(self, valuation_date: date) -> dict:
        return {
            "deal_id": f"team1:{self.deal_id}",
            "source_deal_id": self.deal_id,
            "currency": self.currency,
            "source": self.source,
            "input_type": "team1_payments",
            "demo_assumptions": self.demo,
            "valuation_date": valuation_date.isoformat(),
            "payment_count": len(self.rows),
            "future_payment_count": sum(row["payment_date"] > valuation_date.isoformat() for row in self.rows),
        }


def load_team1_payments(path: Path, currency: str, *, demo: bool = False) -> list[Team1Schedule]:
    """Reject malformed rows, preserving signs and source components."""
    by_deal = defaultdict(list)
    amount_fields = (*CASHFLOW_TYPES, "payment", "closing_balance")
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if tuple(reader.fieldnames or ()) != PAYMENT_FIELDS:
            raise ValueError("Команда 1: нужен payments.csv из команды team1-events.")
        for row in reader:
            try:
                if None in row or not row["deal_id"] or not row["deal_id"].strip():
                    raise ValueError("некорректная строка или нет ID сделки")
                deal_id = row["deal_id"]
                iso_date = date.fromisoformat(row["payment_date"]).isoformat()
                if iso_date != row["payment_date"]:
                    raise ValueError("дата платежа должна иметь формат ГГГГ-ММ-ДД")
                if by_deal[deal_id] and iso_date <= by_deal[deal_id][-1]["payment_date"]:
                    raise ValueError("даты сделки должны возрастать без повторов")
                amounts = {field: Decimal(row[field]) for field in amount_fields}
                if any(not value.is_finite() or value != value.quantize(Decimal("0.01")) for value in amounts.values()):
                    raise ValueError("суммы должны быть конечными и иметь точность до сотых")
                if amounts["payment"] != sum(amounts[field] for field in CASHFLOW_TYPES):
                    raise ValueError("сумма платежа не совпадает с суммой компонентов")
                by_deal[deal_id].append({"payment_date": iso_date, **{key: float(value) for key, value in amounts.items()}})
            except (InvalidOperation, TypeError, ValueError) as error:
                raise ValueError(f"Команда 1, строка {reader.line_num}: {error}.") from error
    if not by_deal:
        raise ValueError("В payments.csv отсутствуют платежи.")
    return [
        Team1Schedule(deal_id, currency, f"Команда 1 · {path.name}", demo, tuple(rows))
        for deal_id, rows in by_deal.items()
    ]
