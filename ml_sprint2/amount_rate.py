def amount_rate_plan(principal: float, rate: float) -> list[dict]:
    result = []
    for suffix, scale in (("minus10", .9), ("plus10", 1.1)):
        result.append({"experiment_id": f"principal_{suffix}", "family": "principal", "cell": "F3",
                       "old_value": principal, "new_value": principal * scale, "optional": False})
    result.extend(rate_experiments(rate, optional=False))
    return result


def rate_experiments(baseline_rate: float, optional: bool) -> list[dict]:
    points = (("070", .070), ("080", .080)) if optional else (("065", .065), ("085", .085))
    return [{"experiment_id": f"rate_{suffix}", "family": "contract_rate", "cell": "J3",
             "old_value": baseline_rate, "new_value": value, "optional": optional} for suffix, value in points]
