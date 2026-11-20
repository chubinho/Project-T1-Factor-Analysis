from dataclasses import replace
from datetime import date
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from openpyxl import Workbook

from ml_sprint3.analysis import compare, demo_experiments, evaluate
from ml_sprint3.curves import Curve, CurveSet, SCENARIO_ROWS, load_irrbb_workbook
from ml_sprint3.deal import Deal, build_schedule


class Sprint3Tests(unittest.TestCase):
    def setUp(self):
        self.start = date(2028, 1, 31)
        self.deal = Deal.from_dict({
            "deal_id": "TEST", "principal": 1200, "currency": "RUB",
            "start_date": self.start.isoformat(), "term_months": 5, "payment_interval_months": 2,
            "payment_type": "ANN", "day_count": "ACT/365",
            "rate_type": "FIXED", "annual_rate": 0.12, "source": "Test fixture",
        })
        self.curves = CurveSet(
            (0.25, 1.0), ("short", "annual"),
            Curve("base", "Base", (0.1, 0.15), "B30:C30"),
            (Curve("up", "Up", (0.14, 0.19), "B36:C36"),),
            "RUB", None, "test.xlsx", "test hash",
        )

    def test_payment_dates_maturity_stub_and_month_end(self):
        self.assertEqual(self.deal.payment_dates,
                         (date(2028, 3, 31), date(2028, 5, 31), date(2028, 6, 30)))
        self.assertEqual([row["days"] for row in build_schedule(self.deal)], [60, 61, 30])
        february = self.deal.to_dict()
        del february["payment_dates"]
        february.update(term_months=2, payment_interval_months=1)
        self.assertEqual(Deal.from_dict(february).payment_dates,
                         (date(2028, 2, 29), date(2028, 3, 31)))

    def test_schedule_method_and_closing_principal(self):
        for method in ("ANN", "RGM", "SIMPLE"):
            with self.subTest(method=method):
                schedule = build_schedule(replace(self.deal, payment_type=method))
                self.assertAlmostEqual(sum(row["principal"] for row in schedule), self.deal.principal)
                self.assertEqual(schedule[-1]["closing_balance"], 0)
                for row in schedule:
                    self.assertAlmostEqual(row["payment"], row["interest"] + row["principal"])
                    self.assertAlmostEqual(row["interest"], row["opening_balance"] * 0.12 * row["days"] / 365)
                if method == "ANN":
                    periods = [1 + 0.12 * days / 365 for days in (60, 61, 30)]
                    factors = sum(1 / math.prod(periods[:n]) for n in range(1, 4))
                    for row in schedule:
                        self.assertAlmostEqual(row["payment"], 1200 / factors)
                elif method == "RGM":
                    self.assertEqual([row["principal"] for row in schedule], [400, 400, 400])
                else:
                    self.assertEqual([row["principal"] for row in schedule], [0, 0, 1200])

    def test_day_count_changes_interest_with_same_discounting(self):
        result = compare(self.deal, self.curves, self.start, "up", day_count="ACT/360")
        before, after = result["before"]["schedule"], result["after"]["schedule"]
        self.assertAlmostEqual(before[0]["interest"], 1200 * 0.12 * 60 / 365)
        self.assertAlmostEqual(after[0]["interest"], 1200 * 0.12 * 60 / 360)
        self.assertEqual([r["discounting"]["base_df"] for r in before],
                         [r["discounting"]["base_df"] for r in after])
        self.assertEqual(result["changed_parameters"], [
            {"parameter": "day_count", "before": "ACT/365", "after": "ACT/360"},
        ])
        self.assertGreater(result["delta_metrics"]["total_interest"], 0)

    def test_valuation_filters_past_and_same_day_and_interpolates_rates(self):
        controls = evaluate(self.deal, self.curves, self.start, "up")
        first, second, _ = controls["schedule"]
        self.assertEqual(first["discounting"]["base_rate"], 0.1)
        tenor = 121 / 365
        expected_rate = 0.1 + (0.15 - 0.1) * (tenor - 0.25) / 0.75
        self.assertAlmostEqual(second["discounting"]["base_df"], math.exp(-expected_rate * tenor))
        on_first_payment = evaluate(self.deal, self.curves, date(2028, 3, 31), "up")
        self.assertIsNone(on_first_payment["schedule"][0]["discounting"])
        self.assertEqual(on_first_payment["metrics"]["future_payment_count"], 2)
        matured = evaluate(self.deal, self.curves, date(2029, 1, 1), "up")
        self.assertEqual(matured["metrics"]["base_pv"], 0)
        self.assertEqual(self.curves.rate(self.curves.base, 1), 0.15)
        self.assertEqual(self.curves.rate(self.curves.base, 30), 0.15)

    def test_demo_varies_one_input_and_reuses_same_fixed_schedule_under_stress(self):
        result = demo_experiments(self.deal, self.curves, self.start, "up")
        self.assertEqual([(e["before_state"], e["after_state"]) for e in result["experiments"]],
                         [("control", "rgm"), ("control", "act360"), ("no_stress", "control")])
        self.assertEqual([e["changed_parameters"][0]["parameter"] for e in result["experiments"]],
                         ["payment_type", "day_count", "scenario_id"])
        control = result["states"]["control"]
        no_stress = result["states"]["no_stress"]
        self.assertEqual(control["deal"], no_stress["deal"])
        self.assertEqual([row["payment"] for row in control["schedule"]],
                         [row["payment"] for row in no_stress["schedule"]])
        metrics = control["metrics"]
        self.assertEqual(metrics["dnpv"], metrics["stress_pv"] - metrics["base_pv"])
        self.assertLess(metrics["dnpv"], 0)
        self.assertEqual(no_stress["metrics"]["dnpv"], 0)

    def test_invalid_and_missing_inputs(self):
        with self.assertRaisesRegex(ValueError, "Floating-rate"):
            replace(self.deal, rate_type="FLOAT")
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            replace(self.deal, payment_dates=(self.start,))
        with self.assertRaisesRegex(ValueError, "currency"):
            evaluate(replace(self.deal, currency="USD"), self.curves, self.start, "up")
        with self.assertRaisesRegex(ValueError, "Curve date"):
            evaluate(self.deal, replace(self.curves, as_of=date(2028, 2, 1)), self.start, "up")
        with self.assertRaisesRegex(ValueError, "Unknown scenario"):
            evaluate(self.deal, self.curves, self.start, "missing")

    def test_import_curve_cells_units_floor_and_missing_cache(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "curve.xlsx"
            book = Workbook()
            sheet = book.active
            sheet.title = "IRRBB"
            sheet["A30"] = "Базовая кривая G-curve, %"
            for index in range(1, 20):
                col = index + 1
                for row, value in ((23, index), (24, str(index)), (25, index / 2), (29, -1.5), (30, 10)):
                    sheet.cell(row, col, value)
                for scenario, (row, source_name) in SCENARIO_ROWS.items():
                    sheet.cell(row - 2, 1, source_name)
                    sheet.cell(row, 1, "Кривая под стрессом, %")
                    shock = -20 if scenario == "parallel_down" else 4
                    sheet.cell(row - 1, col, shock)
                    sheet.cell(row, col, -1.5 if shock < 0 else 14)
            book.save(path)
            result = load_irrbb_workbook(path)
            self.assertEqual(result.base.rates, (0.1,) * 19)
            self.assertEqual(result.scenario("parallel_up").rates, (0.14,) * 19)
            self.assertEqual(result.scenario("parallel_down").rates, (-0.015,) * 19)
            self.assertIsNone(result.to_dict()["source"]["as_of"])
            sheet["T36"] = 18
            book.save(path)
            with self.assertRaisesRegex(ValueError, "stress value differs"):
                load_irrbb_workbook(path)
            sheet["T36"] = "=4+10"  # openpyxl writes formulas without cached values.
            book.save(path)
            with self.assertRaisesRegex(ValueError, "IRRBB row 36, column 20"):
                load_irrbb_workbook(path)
            sheet["A34"] = "parallel down"
            book.save(path)
            with self.assertRaisesRegex(ValueError, "expected scenario parallel up"):
                load_irrbb_workbook(path)
            book.close()


if __name__ == "__main__":
    unittest.main()
