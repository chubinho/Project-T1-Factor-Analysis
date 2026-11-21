"""Проверки общего графика команды 1."""

from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from de_sprint3.team1_events import normalize_events


class Team1EventsTest(unittest.TestCase):
    def parse(self, rows):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "result.txt"
            path.write_text(
                "deal_id дд.мм.гггг тип сумма остаток_после дни_от_начала_периода\n"
                + rows, encoding="utf-8",
            )
            return normalize_events(path, "test")

    def test_cashflow_and_repricing_keep_separate_units(self):
        events, payments, report = self.parse(
            "1 1.1.2026 interest 10.00 90.00 1\n"
            "1 1.1.2026 prepayment 10.00 90.00 1\n"
            "1 2.1.2026 repricing 0.12345 90.00 2\n"
            "1 3.1.2026 interest 5.00 0.00 3\n"
            "1 3.1.2026 subsidy 2.00 0.00 2\n"
            "1 3.1.2026 close 90.00 0.00 3\n"
            "2 1.1.2026 interest -2.00 -10.00 1\n"
            "2 2.1.2026 close -10.00 0.00 2\n"
        )
        self.assertEqual(len(payments), 4)
        self.assertEqual(events[2]["repricing_value_raw"], Decimal("0.12345"))
        self.assertEqual(payments[1]["payment"], Decimal("97.00"))
        self.assertEqual(payments[-1]["payment"], Decimal("-10.00"))
        self.assertEqual(report["different_day_counters_on_same_date"],
                         [{"deal_id": "1", "payment_date": "2026-01-03"}])
        self.assertEqual(report["balance_differences_above_one_cent"], [])

    def test_detects_mismatched_balance_rollforward(self):
        _, _, report = self.parse(
            "1 1.1.2026 interest 10.00 90.00 1\n"
            "1 2.1.2026 close 70.00 0.00 2\n"
        )
        self.assertEqual(report["balance_differences_above_one_cent"][0]["residual"], "20.00")

    def test_rejects_prepayment_without_interest_on_same_date(self):
        with self.assertRaisesRegex(ValueError, "без события interest"):
            self.parse("1 1.1.2026 prepayment 20.00 90.00 1\n")


if __name__ == "__main__":
    unittest.main()
