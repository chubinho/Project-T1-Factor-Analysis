from datetime import date
import json
from pathlib import Path
import unittest

from ai_sprint3.__main__ import Dashboard, GENERATOR_CONFIG, generate_deals, load_deals, load_snapshot
from ml_sprint3.deal import Deal


class DashboardGeneratorTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(GENERATOR_CONFIG.read_text(encoding="utf-8"))
        self.config.update(
            count=2,
            principal_rub=[725_000, 725_000],
            annual_rate=[0.15, 0.15],
            term_months_weights={"17": 1},
            payment_type_weights={"ANN": 0, "RGM": 1},
            rate_type_weights={"FIXED": 1, "FLOAT": 0},
            day_count_weights={"ACT/ACT ISDA": 1},
        )

    def test_generated_deal_is_reproducible_and_compares_independently(self):
        rows = generate_deals(self.config)
        self.assertEqual(rows, generate_deals(self.config))
        self.assertEqual(len(rows), 2)
        deal = Deal.from_dict(rows[0])
        self.assertEqual(deal.deal_id, "S3-GEN-0001")
        self.assertEqual(deal.principal, 725_000)
        self.assertEqual(deal.annual_rate, 0.15)
        self.assertEqual(deal.payment_type, "RGM")
        self.assertEqual(deal.day_count, "ACT/ACT ISDA")
        self.assertEqual(len(deal.payment_dates), 17)

        control = load_deals(Path("outputs/de_sprint3/deals_fixed.csv"))
        curves, market = load_snapshot(Path("outputs/ml_sprint3/results.json"), None)
        dashboard = Dashboard(control, curves, market, date(2026, 11, 20))
        query = {"deal": [deal.deal_id], "payment_type": ["ANN"],
                 "day_count": ["ACT/ACT ISDA"], "scenario": ["parallel_up"]}
        result = dashboard.calculate(query, generated_deal=deal)
        self.assertAlmostEqual(result["before"]["metrics"]["total_principal"], 725_000, places=6)
        self.assertEqual(result["before"]["deal"]["payment_type"], "RGM")
        self.assertEqual(result["after"]["deal"]["payment_type"], "ANN")
        self.assertEqual(result["after"]["metrics"]["final_balance"], 0)
        self.assertLess(result["after"]["metrics"]["dnpv"], 0)
        self.assertNotEqual(dashboard.calculate(query)["before"]["deal"]["principal"], deal.principal)

    def test_float_weights_generate_float_parameters(self):
        self.config["rate_type_weights"] = {"FIXED": 0, "FLOAT": 1}
        self.config["float_rate"] = {"index": "TEST_INDEX", "spread": 0.013, "reset_interval_months": 2}
        for deal in generate_deals(self.config):
            self.assertEqual(deal["rate_type"], "FLOAT")
            self.assertEqual(deal["rate_index"], "TEST_INDEX")
            self.assertEqual(deal["rate_spread"], 0.013)
            self.assertEqual(deal["reset_interval_months"], 2)

    def test_rejects_empty_distribution_or_excessive_work(self):
        self.config["term_months_weights"] = {"17": 0}
        with self.assertRaisesRegex(ValueError, "term_months_weights"):
            generate_deals(self.config)
        self.config.update(count=500, term_months_weights={"121": 1})
        with self.assertRaisesRegex(ValueError, "максимальный срок"):
            generate_deals(self.config)
