import csv
from datetime import date
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ai_sprint3.__main__ import Dashboard
from de_sprint3.team1_events import PAYMENT_FIELDS
from de_sprint3.team1_payments import load_team1_payments
from ml_sprint3.curves import Curve, CurveSet


class Team1ValuationTests(unittest.TestCase):
    def setUp(self):
        self.valuation_date = date(2026, 1, 1)
        self.curves = CurveSet(
            (1., 2.), ("1y", "2y"),
            Curve("base", "Base", (0., 0.), "B30:C30"),
            (Curve("stress", "Stress", (.1, .1), "B36:C36"),),
            "RUB", self.valuation_date, "training.xlsx", "test",
        )
        self.rows = [
            ("DEMO", "2025-12-31", 100, 0, 0, 0, 0, 100, 120),
            ("DEMO", "2026-01-01", 100, 0, 0, 0, 0, 100, 20),
            ("DEMO", "2027-01-01", 10, 100, 5, 20, 5, 140, 0),
            ("DEMO", "2028-01-01", 0, -10, 0, 0, 0, -10, 0),
            ("PAST", "2025-12-31", 0, 10, 0, 0, 0, 10, 0),
        ]
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "payments.csv"

    def load(self, currency="RUB"):
        with self.path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(PAYMENT_FIELDS)
            writer.writerows(self.rows)
        return load_team1_payments(self.path, currency, demo=True)

    def dashboard(self, currency="RUB"):
        return Dashboard([], self.curves, self.curves.to_dict(), self.valuation_date, team1=self.load(currency))

    def test_signed_components_and_cutoff_in_base_and_stress(self):
        dashboard = self.dashboard()
        self.assertEqual(dashboard.options()["team1_deals"][0]["future_payment_count"], 2)
        result = dashboard.calculate({"deal": ["team1:DEMO"], "scenario": ["stress"]})
        after = result["after"]
        self.assertTrue(after["deal"]["demo_assumptions"])
        self.assertEqual(after["metrics"]["base_pv"], 130)
        expected_stress = 140 * math.exp(-.1) - 10 * math.exp(-.2)
        self.assertAlmostEqual(after["metrics"]["stress_pv"], expected_stress)
        self.assertAlmostEqual(after["metrics"]["dnpv"], expected_stress - 130)
        self.assertEqual([x["payment"] for x in after["schedule"]], [100, 100, 140, -10])
        self.assertIsNone(after["schedule"][1]["discounting"])
        self.assertEqual(after["schedule"][2]["subsidy"], 20)
        self.assertEqual(after["schedule"][2]["prepayment"], 5)
        self.assertEqual([x["parameter"] for x in result["changed_parameters"]], ["scenario_id"])
        self.assertNotIn("discounting", dashboard.team1["team1:DEMO"].rows[0])

    def test_no_future_payments_and_wrong_currency(self):
        result = self.dashboard().calculate({"deal": ["team1:PAST"], "scenario": ["stress"]})
        self.assertEqual(result["after"]["metrics"]["future_payment_count"], 0)
        self.assertEqual(result["after"]["metrics"]["dnpv"], 0)
        with self.assertRaisesRegex(ValueError, "должна совпадать"):
            self.dashboard("USD")

    def test_wrong_component_total_and_repeated_date(self):
        self.rows[1] = (*self.rows[1][:-2], 101, 20)
        with self.assertRaisesRegex(ValueError, "суммой компонентов"):
            self.load()
        self.rows[1] = self.rows[0]
        with self.assertRaisesRegex(ValueError, "без повторов"):
            self.load()
