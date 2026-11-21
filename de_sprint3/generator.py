"""Воспроизводимая генерация параметров синтетических сделок."""

import random

from ml_sprint3.deal import DAY_COUNTS, PAYMENT_TYPES, Deal, finite_number, monthly_dates, parse_date


def weighted(rng, distribution, allowed, field):
    if not distribution or set(distribution) - set(allowed):
        raise ValueError(f"{field}: неизвестные значения или пустое распределение")
    pairs = sorted(distribution.items())
    weights = [finite_number(weight, field) for _, weight in pairs]
    if min(weights) < 0 or sum(weights) <= 0:
        raise ValueError(f"{field}: веса должны быть неотрицательными, хотя бы один положительный")
    return rng.choices([value for value, _ in pairs], weights)[0]


def generate(config):
    """Сгенерировать заданное число сделок с постоянными ID для одного seed."""
    count, seed = config["count"], config["seed"]
    if type(count) is not int or count < 1 or type(seed) is not int:
        raise ValueError("count должен быть положительным целым числом, seed — целым числом")
    rng = random.Random(seed)
    amount_min, amount_max = config["principal_rub"]
    if any(type(amount) is not int for amount in (amount_min, amount_max)) or not 0 < amount_min <= amount_max:
        raise ValueError("principal_rub: нужны две возрастающие положительные суммы в целых рублях")
    rate_min, rate_max = (finite_number(value, "annual_rate") for value in config["annual_rate"])
    if not 0 <= rate_min <= rate_max:
        raise ValueError("annual_rate: нужны неотрицательные границы ставки в долях")
    start = parse_date(config["start_date"])
    interval = config["payment_interval_months"]
    terms = config["term_months_weights"]
    if any(int(term) <= 0 or str(int(term)) != term for term in terms):
        raise ValueError("term_months_weights: сроки должны быть положительными целыми месяцами")
    rate_types = config["rate_type_weights"]
    if rate_types.get("FLOAT", 0):
        float_config = config["float_rate"]
        if not isinstance(float_config["index"], str) or not float_config["index"].strip():
            raise ValueError("float_rate.index: укажите индекс")
        finite_number(float_config["spread"], "float_rate.spread")
        reset = float_config["reset_interval_months"]
        if type(reset) is not int or reset < 1:
            raise ValueError("float_rate.reset_interval_months: укажите положительное число месяцев")

    deals = []
    for index in range(1, count + 1):
        term = int(weighted(rng, terms, terms, "term_months_weights"))
        monthly_dates(start, term, interval)
        deal = {
            "deal_id": f"S3-GEN-{index:04d}",
            "source": "Учебный генератор спринта 3",
            "principal": rng.randint(amount_min, amount_max),
            "currency": "RUB",
            "start_date": start.isoformat(),
            "term_months": term,
            "payment_interval_months": interval,
            "payment_type": weighted(rng, config["payment_type_weights"], PAYMENT_TYPES, "payment_type_weights"),
            "day_count": weighted(rng, config["day_count_weights"], DAY_COUNTS, "day_count_weights"),
            "rate_type": weighted(rng, rate_types, ("FIXED", "FLOAT"), "rate_type_weights"),
            "annual_rate": round(rng.uniform(rate_min, rate_max), 8),
        }
        if deal["rate_type"] == "FIXED":
            Deal.from_dict(deal)
        else:
            deal.update(rate_index=float_config["index"], rate_spread=float_config["spread"],
                        reset_interval_months=float_config["reset_interval_months"])
        deals.append(deal)
    return deals
