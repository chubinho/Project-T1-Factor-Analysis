from copy import deepcopy
import math
from pathlib import Path
import tempfile
import unittest

from ml_sprint2.analysis import analyse, annuity, write_results
from ml_sprint2.contract import DF_COLUMNS, number, read_contract
from ml_sprint2.experiments import make_plan


def synthetic_capture(include_half_point=False):
    p, rate, n = 100_000., .075, 334
    columns = DF_COLUMNS[1:]
    buckets = [{"column":col, "label":f"bucket_{i}", "base_cell":f"{col}9", "stress_cell":f"{col}11",
                "base_df":.98-i*.02, "stress_df":.98-i*.02+(.01 if i<8 else -.03)} for i,col in enumerate(DF_COLUMNS)]
    def payment(principal, r):
        m=r/12
        return principal*m/(1-(1+m)**(-n)) if r else principal/n
    base_payment=payment(p,rate)
    flows=[{"source_row":19+i,"payment_date":"2022-09-22","df_column":columns[i%18],"cached_cashflow":base_payment} for i in range(n)]
    contract={"source_name":"synthetic_fixture.xlsx","source_sha256":"a"*64,"valuation_date":"2022-08-31","currency":"RUB",
              "principal":p,"contract_rate":rate,"payments_count":n,"buckets":buckets,"flows":flows,
              "cached_final_balance":0.,"team_acceptance":"synthetic_test_only","limitations":["Test fixture, not real case data"]}
    plan=make_plan(contract,include_half_point)
    bucket_map={b["column"]:b for b in buckets}
    results=[]
    for change in plan:
        principal=change["new_value"] if change["family"]=="principal" else p
        r=change["new_value"] if change["family"]=="contract_rate" else rate
        cf=payment(principal,r)
        base=sum(cf*(change["new_value"] if change["family"]=="df" and change["df_column"]==row["df_column"] else bucket_map[row["df_column"]]["base_df"]) for row in flows)
        stress=sum(cf*bucket_map[row["df_column"]]["stress_df"] for row in flows)
        metrics={"base_npv":base,"stress_npv":stress,"dnpv":stress-base,"regular_payment":cf,"final_balance":0.,"cashflows":[cf]*n}
        results.append({"experiment_id":change["experiment_id"],"metrics":metrics,
                        "changed_cells":[] if change["cell"] is None else [change["cell"]],
                        "baseline_grid_sha256":"b"*64,"restored_grid_sha256":"b"*64,
                        "baseline_before_verified":True,"restored_after_verified":True})
    baseline=deepcopy(results[0]["metrics"])
    contract["cached_outputs"]={k:baseline[k] for k in ("base_npv","stress_npv","dnpv")}
    capture={"source_sha256":"a"*64,"baseline":baseline,"experiments":results,"engine":"synthetic_test","excel_version":"not_used"}
    return contract,plan,capture


class PlanAndMathTests(unittest.TestCase):
    def test_exact_required_experiments_and_no_h_df(self):
        contract,plan,_=synthetic_capture()
        self.assertEqual(len(plan),24)
        self.assertEqual(len([p for p in plan if p["family"]=="df"]),19)
        self.assertNotIn("H9",[p["cell"] for p in plan])
        self.assertEqual([p["old_value"] for p in plan if p["cell"]=="F3"],[100_000.,100_000.])

    def test_optional_experiments_are_separate(self):
        c,p,raw=synthetic_capture(True)
        result=analyse(c,p,raw)
        self.assertEqual(len(result["journal"]),24)
        self.assertEqual(len(result["optional_rate_experiments"]),2)

    def test_zero_rate_and_near_zero_rate(self):
        self.assertEqual(annuity(1200,0,12),100)
        self.assertAlmostEqual(annuity(1200,1e-15,12),100,places=10)

    def test_invalid_numbers_rejected(self):
        for value in (None,True,"1",math.nan,math.inf):
            with self.subTest(value=value),self.assertRaises(ValueError):
                number(value,"input")
        for p,r,n in ((0,.1,12),(100,-.1,12),(100,.1,0),(math.inf,.1,12)):
            with self.subTest(values=(p,r,n)),self.assertRaises(ValueError):
                annuity(p,r,n)

    def test_synthetic_df_balance_and_opposite_signs(self):
        c,p,raw=synthetic_capture()
        result=analyse(c,p,raw)
        df=[r for r in result["journal"] if r["family"]=="df"]
        self.assertTrue(any(r["df_stress_contribution"]>0 for r in df))
        self.assertTrue(any(r["df_stress_contribution"]<0 for r in df))
        self.assertEqual(result["zero_df_buckets"],["bucket_0"])
        self.assertAlmostEqual(sum(r["df_stress_contribution"] for r in df),raw["baseline"]["dnpv"],places=7)
        for row in df:
            self.assertAlmostEqual(row["delta_dnpv"],-row["df_stress_contribution"],places=7)

    def test_empty_and_wrong_workbook_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            wrong=Path(folder)/"not-a-workbook.txt"
            wrong.write_text("test")
            with self.assertRaisesRegex(ValueError,"xlsx"):
                read_contract(wrong)

    def test_csv_rows_roundtrip_without_rounding(self):
        import csv
        c,p,raw=synthetic_capture(True)
        result=analyse(c,p,raw)
        with tempfile.TemporaryDirectory() as folder:
            write_results(Path(folder),result)
            with (Path(folder)/"journal.csv").open(encoding="utf-8-sig") as f:
                rows=list(csv.DictReader(f))
            self.assertEqual(len(rows),24)
            self.assertEqual(float(rows[0]["base_npv"]),raw["baseline"]["base_npv"])
            self.assertEqual(rows[0]["df_stress_contribution"],"")


class InvalidEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.c,self.p,self.raw=synthetic_capture()

    def assert_invalid(self,pattern):
        with self.assertRaisesRegex(ValueError,pattern):
            analyse(self.c,self.p,self.raw)

    def test_wrong_source_hash(self):
        self.raw["source_sha256"]="different"
        self.assert_invalid("hashes")

    def test_missing_experiment(self):
        self.raw["experiments"].pop()
        self.assert_invalid("experiment IDs")

    def test_duplicate_experiment(self):
        self.raw["experiments"][-1]=deepcopy(self.raw["experiments"][-2])
        self.assert_invalid("experiment IDs")

    def test_reordered_experiments(self):
        self.raw["experiments"][1:3]=reversed(self.raw["experiments"][1:3])
        self.assert_invalid("experiment IDs")

    def test_two_independent_inputs_changed(self):
        self.raw["experiments"][1]["changed_cells"]=["F3","J3"]
        self.assert_invalid("OAT")

    def test_restoration_not_verified(self):
        self.raw["experiments"][1]["restored_after_verified"]=False
        self.assert_invalid("restoration evidence")

    def test_formula_grid_changed(self):
        self.raw["experiments"][1]["restored_grid_sha256"]="bad"
        self.assert_invalid("grid changed")

    def test_stale_valuation(self):
        self.raw["experiments"][1]["metrics"]["base_npv"]+=1
        self.assert_invalid("dot product")

    def test_nan_cashflow(self):
        self.raw["experiments"][1]["metrics"]["cashflows"][0]=math.nan
        self.assert_invalid("finite")

    def test_wrong_cashflow_count(self):
        self.raw["experiments"][1]["metrics"]["cashflows"].pop()
        self.assert_invalid("cashflow count")

    def test_cached_baseline_drift(self):
        self.c["cached_outputs"]["base_npv"]+=1
        self.assert_invalid("Recalculated vs supplied")

    def test_wrong_dnpv_sign(self):
        self.raw["experiments"][1]["metrics"]["dnpv"]*=-1
        self.assert_invalid("dot product")


if __name__=="__main__":
    unittest.main()
