
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

from .contract import MONEY_TOLERANCE, number
from .df import fixed_cashflow_contributions


def require_close(actual: float, expected: float, label: str) -> None:
    if not math.isclose(actual, expected, abs_tol=MONEY_TOLERANCE, rel_tol=0):
        raise ValueError(f"{label}: {actual!r} != {expected!r} (tolerance {MONEY_TOLERANCE} RUB)")


def annuity(principal: float, annual_rate: float, periods: int) -> float:
    if not math.isfinite(principal) or not math.isfinite(annual_rate) or principal <= 0 or periods <= 0 or annual_rate < 0:
        raise ValueError("Expected positive principal/periods and finite nonnegative rate")
    monthly = annual_rate / 12
    if monthly == 0:
        return principal / periods
    return principal * monthly / -math.expm1(-periods * math.log1p(monthly))


def analyse(contract: dict, plan: list[dict], capture: dict) -> dict:
    if capture.get("source_sha256") != contract["source_sha256"]:
        raise ValueError("Capture and contract source hashes differ")
    actual = capture.get("experiments", [])
    if [r["experiment_id"] for r in actual] != [p["experiment_id"] for p in plan]:
        raise ValueError("Missing, duplicated, reordered or unexpected experiment IDs")
    if len([p for p in plan if not p["optional"]]) != 24:
        raise ValueError("Expected exactly 24 required experiments")
    base = capture["baseline"]
    for key in ("base_npv", "stress_npv", "dnpv"):
        require_close(number(base[key], key), contract["cached_outputs"][key], f"Recalculated vs supplied {key}")
    require_close(number(base["final_balance"], "final_balance"), contract["cached_final_balance"], "Final debt")
    baseline_flows = base["cashflows"]
    if len(baseline_flows) != len(contract["flows"]):
        raise ValueError("Wrong baseline cashflow count")
    for value, row in zip(baseline_flows, contract["flows"]):
        require_close(number(value, "cashflow"), row["cached_cashflow"], f"Cached vs Excel G{row['source_row']}")
    buckets = {b["column"]: b for b in contract["buckets"]}
    expected_df = fixed_cashflow_contributions(baseline_flows, contract["flows"], contract["buckets"])
    journal, optional = [], []
    largest_arithmetic_error = 0.0
    baseline_hash = actual[0].get("baseline_grid_sha256")
    if not baseline_hash:
        raise ValueError("Missing baseline grid fingerprint")
    for p, observed in zip(plan, actual):
        if observed.get("baseline_before_verified") is not True or observed.get("restored_after_verified") is not True:
            raise ValueError(f"{p['experiment_id']}: missing baseline/restoration evidence")
        if observed.get("baseline_grid_sha256") != baseline_hash or observed.get("restored_grid_sha256") != baseline_hash:
            raise ValueError("Formula/input grid changed between experiments")
        expected_cells = [] if p["cell"] is None or p["old_value"] == p["new_value"] else [p["cell"]]
        if observed.get("changed_cells") != expected_cells:
            raise ValueError(f"{p['experiment_id']}: OAT violation")
        metrics = observed["metrics"]
        for key in ("base_npv", "stress_npv", "dnpv", "regular_payment", "final_balance"):
            number(metrics[key], f"{p['experiment_id']}.{key}")
        principal = p["new_value"] if p["family"] == "principal" else contract["principal"]
        rate = p["new_value"] if p["family"] == "contract_rate" else contract["contract_rate"]
        flow_values = metrics["cashflows"]
        if len(flow_values) != contract["payments_count"]:
            raise ValueError("Wrong trial cashflow count")
        expected_payment = annuity(principal, rate, contract["payments_count"])
        require_close(metrics["regular_payment"], expected_payment, "Annuity payment")
        for cf in flow_values:
            require_close(number(cf, "cashflow"), expected_payment, "Recalculated payment identity")
        independent_base, independent_stress = [], []
        for cf, flow in zip(flow_values, contract["flows"]):
            bucket = buckets[flow["df_column"]]
            base_df = p["new_value"] if p["family"] == "df" and p["df_column"] == bucket["column"] else bucket["base_df"]
            independent_base.append(cf * base_df)
            independent_stress.append(cf * bucket["stress_df"])
        expected_base, expected_stress = math.fsum(independent_base), math.fsum(independent_stress)
        for key, expected in (("base_npv", expected_base), ("stress_npv", expected_stress), ("dnpv", expected_stress-expected_base)):
            largest_arithmetic_error = max(largest_arithmetic_error, abs(metrics[key]-expected))
            require_close(metrics[key], expected, f"Independent dot product {p['experiment_id']}.{key}")
        require_close(metrics["dnpv"], metrics["stress_npv"]-metrics["base_npv"], "dNPV definition")
        delta_base = metrics["base_npv"] - base["base_npv"]
        delta_stress = metrics["stress_npv"] - base["stress_npv"]
        delta_dnpv = metrics["dnpv"] - base["dnpv"]
        contribution = None
        if p["family"] == "df":
            contribution = expected_df[p["df_column"]]
            require_close(delta_base, contribution, "DF contribution vs base delta")
            require_close(delta_stress, 0, "Stress NPV fixed during base-DF experiment")
            require_close(delta_dnpv, -contribution, "Opposite signs of DF contribution and dNPV delta")
        elif p["family"] == "principal":
            scale = principal / contract["principal"]
            for key in ("base_npv", "stress_npv", "dnpv", "regular_payment", "final_balance"):
                require_close(metrics[key], base[key]*scale, f"Principal homogeneity {key}")
        row = {"experiment_id": p["experiment_id"], "family": p["family"], "input_cell": p["cell"],
               "old_value": p["old_value"], "new_value": p["new_value"], "bucket": p.get("bucket"),
               "principal": principal, "contract_rate": rate, "base_npv": metrics["base_npv"],
               "stress_npv": metrics["stress_npv"], "dnpv": metrics["dnpv"],
               "delta_base_npv": delta_base, "delta_stress_npv": delta_stress, "delta_dnpv": delta_dnpv,
               "df_stress_contribution": contribution, "regular_payment": metrics["regular_payment"],
               "final_balance": metrics["final_balance"], "currency": contract["currency"],
               "valuation_date": contract["valuation_date"], "oat_verified": True,
               "baseline_restored": True, "source_sha256": contract["source_sha256"]}
        (optional if p["optional"] else journal).append(row)
    df_rows = [r for r in journal if r["family"] == "df"]
    total = math.fsum(r["df_stress_contribution"] for r in df_rows)
    residual = base["dnpv"] - total
    require_close(residual, 0, "DF balance")
    ranking = sorted(df_rows, key=lambda r: abs(r["df_stress_contribution"]), reverse=True)
    rates = sorted([r for r in journal if r["family"] in ("control", "contract_rate")]+optional, key=lambda r:r["contract_rate"])
    for lower, upper in zip(rates, rates[1:]):
        if not lower["regular_payment"] < upper["regular_payment"]:
            raise ValueError("Rate/payment monotonicity failed")
    return {"schema_version": 1, "source": {k:contract[k] for k in ("source_name", "source_sha256", "valuation_date", "currency")},
            "engine": capture["engine"], "excel_version": capture["excel_version"],
            "team_acceptance": contract["team_acceptance"], "limitations": contract["limitations"],
            "journal": journal, "optional_rate_experiments": optional,
            "df_balance": {"contributions_sum": total, "initial_dnpv": base["dnpv"], "residual": residual,
                           "absolute_tolerance_rub": MONEY_TOLERANCE, "passed": True},
            "largest_df_effect": {k:ranking[0][k] for k in ("experiment_id", "bucket", "df_stress_contribution", "delta_dnpv")},
            "zero_df_buckets": [r["bucket"] for r in df_rows if abs(r["df_stress_contribution"]) <= MONEY_TOLERANCE],
            "verification": {"required_records": len(journal), "optional_records": len(optional),
                             "all_oat_and_restores_verified": True, "all_cashflows_verified": True,
                             "max_independent_valuation_error_rub": largest_arithmetic_error}}


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+"\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return

    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_results(output: Path, result: dict) -> None:
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "results.json", result)
    write_json(output / "baseline.json", result["journal"][0])
    write_csv(output / "journal.csv", result["journal"])
    write_csv(output / "amount_rate.csv", [r for r in result["journal"] if r["family"] in ("control", "principal", "contract_rate")])
    write_csv(output / "df_effects.csv", [r for r in result["journal"] if r["family"] == "df"])
    write_csv(output / "optional_rates.csv", result["optional_rate_experiments"])
    write_json(output / "verification.json", {"checks":result["verification"], "df_balance":result["df_balance"]})
