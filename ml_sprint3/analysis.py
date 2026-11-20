"""Value the contractual cash flows and assemble comparable states for the dashboard."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
import math

from .curves import CurveSet
from .deal import Deal, build_schedule


METRIC_LABEL = "PV денежных потоков"
SCHEMA_VERSION = 1


def evaluate(deal: Deal, curves: CurveSet, valuation_date: date, scenario_id: str) -> dict:
    if deal.currency != curves.currency:
        raise ValueError(f"Deal currency {deal.currency} differs from curve currency {curves.currency}")
    if curves.as_of and curves.as_of != valuation_date:
        raise ValueError(f"Curve date {curves.as_of} differs from valuation date {valuation_date}")
    scenario = curves.scenario(scenario_id)
    schedule = build_schedule(deal)
    base_values, stress_values = [], []
    for row, payment_date in zip(schedule, deal.payment_dates):
        days_to_payment = (payment_date - valuation_date).days
        if days_to_payment <= 0:
            row["discounting"] = None
            continue
        tenor = days_to_payment / 365
        base_rate = curves.rate(curves.base, tenor)
        stress_rate = curves.rate(scenario, tenor)
        base_df = math.exp(-base_rate * tenor)
        stress_df = math.exp(-stress_rate * tenor)
        row["discounting"] = {
            "tenor_years": tenor,
            "base_rate": base_rate,
            "stress_rate": stress_rate,
            "base_df": base_df,
            "stress_df": stress_df,
            "base_pv": row["payment"] * base_df,
            "stress_pv": row["payment"] * stress_df,
        }
        base_values.append(row["discounting"]["base_pv"])
        stress_values.append(row["discounting"]["stress_pv"])

    base_pv, stress_pv = math.fsum(base_values), math.fsum(stress_values)
    return {
        "deal": deal.to_dict(),
        "valuation_date": valuation_date.isoformat(),
        "scenario_id": scenario_id,
        "metrics": {
            "total_principal": math.fsum(row["principal"] for row in schedule),
            "total_interest": math.fsum(row["interest"] for row in schedule),
            "total_payments": math.fsum(row["payment"] for row in schedule),
            "final_balance": schedule[-1]["closing_balance"],
            "future_payment_count": len(base_values),
            "base_pv": base_pv,
            "stress_pv": stress_pv,
            "dnpv": stress_pv - base_pv,
        },
        "schedule": schedule,
    }


def _comparison(before: dict, after: dict) -> dict:
    field_names = ("payment_type", "day_count")
    fixed_inputs = {
        key: value for key, value in before["deal"].items() if key not in field_names
    }
    fixed_inputs["valuation_date"] = before["valuation_date"]
    changes = []
    for field in (*field_names, "scenario_id"):
        source_before = before if field == "scenario_id" else before["deal"]
        source_after = after if field == "scenario_id" else after["deal"]
        if source_before[field] == source_after[field]:
            fixed_inputs[field] = source_before[field]
        else:
            changes.append({"parameter": field, "before": source_before[field], "after": source_after[field]})
    return {
        "changed_parameters": changes,
        "fixed_inputs": fixed_inputs,
        "delta_metrics": {key: value - before["metrics"][key] for key, value in after["metrics"].items()},
    }


def compare(
    deal: Deal,
    curves: CurveSet,
    valuation_date: date,
    scenario_id: str,
    *,
    reference_scenario_id: str | None = None,
    payment_type: str | None = None,
    day_count: str | None = None,
) -> dict:
    """Return both full states. Keep the selected scenario fixed unless a reference is supplied."""
    after_deal = replace(
        deal,
        payment_type=payment_type if payment_type is not None else deal.payment_type,
        day_count=day_count if day_count is not None else deal.day_count,
    )
    before_scenario = scenario_id if reference_scenario_id is None else reference_scenario_id
    before = evaluate(deal, curves, valuation_date, before_scenario)
    after = evaluate(after_deal, curves, valuation_date, scenario_id)
    return {
        "schema_version": SCHEMA_VERSION,
        "metric_label": METRIC_LABEL,
        "market": curves.to_dict(),
        **_comparison(before, after),
        "before": before,
        "after": after,
    }


def demo_experiments(deal: Deal, curves: CurveSet, valuation_date: date, scenario_id: str) -> dict:
    if (deal.payment_type, deal.day_count) != ("ANN", "ACT/365"):
        raise ValueError("The demo requires an ANN deal with ACT/365 as the common starting point")
    if scenario_id == "base":
        raise ValueError("Select a stress scenario for the demo")
    states = {
        "control": evaluate(deal, curves, valuation_date, scenario_id),
        "rgm": evaluate(replace(deal, payment_type="RGM"), curves, valuation_date, scenario_id),
        "act360": evaluate(replace(deal, day_count="ACT/360"), curves, valuation_date, scenario_id),
        "no_stress": evaluate(deal, curves, valuation_date, "base"),
    }
    experiments = []
    for experiment, before, after in (
        ("payment_type", "control", "rgm"),
        ("day_count", "control", "act360"),
        ("stress", "no_stress", "control"),
    ):
        experiments.append({
            "experiment_id": experiment,
            "before_state": before,
            "after_state": after,
            **_comparison(states[before], states[after]),
        })
    scenario_metrics = []
    for curve in (curves.base, *curves.stresses):
        if curve.scenario_id == "base":
            state = states["no_stress"]
        elif curve.scenario_id == scenario_id:
            state = states["control"]
        else:
            state = evaluate(deal, curves, valuation_date, curve.scenario_id)
        scenario_metrics.append({"scenario_id": curve.scenario_id, **state["metrics"]})
    return {
        "schema_version": SCHEMA_VERSION,
        "metric_label": METRIC_LABEL,
        "market": curves.to_dict(),
        "control_state": "control",
        "states": states,
        "experiments": experiments,
        "scenario_metrics": scenario_metrics,
    }
