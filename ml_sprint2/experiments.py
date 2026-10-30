from __future__ import annotations

from .amount_rate import amount_rate_plan, rate_experiments
from .df import df_plan


def make_plan(contract: dict, include_half_point: bool = False) -> list[dict]:
    p, r = contract["principal"], contract["contract_rate"]
    plan = [{"experiment_id": "control", "family": "control", "cell": None,
             "old_value": None, "new_value": None, "optional": False}]
    plan.extend(amount_rate_plan(p, r))
    plan.extend(df_plan(contract["buckets"]))
    if include_half_point:
        plan.extend(rate_experiments(r, optional=True))
    return plan
