import math


def df_plan(buckets: list[dict]) -> list[dict]:
    return [{"experiment_id": f"df_{b['column']}", "family": "df", "cell": b["base_cell"],
             "old_value": b["base_df"], "new_value": b["stress_df"], "bucket": b["label"],
             "df_column": b["column"], "optional": False} for b in buckets]


def fixed_cashflow_contributions(cashflows: list[float], flows: list[dict], buckets: list[dict]) -> dict[str, float]:
    if len(cashflows) != len(flows):
        raise ValueError("Cashflows and bucket assignments must have the same length")
    return {b["column"]: math.fsum(cf for cf, flow in zip(cashflows, flows) if flow["df_column"] == b["column"])
            * (b["stress_df"] - b["base_df"]) for b in buckets}
