"""Загрузка событий по всем сделкам из текстовой выгрузки команды 1."""

from collections import Counter, defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from hashlib import sha256

CASHFLOW_TYPES = ("principal", "interest", "prepayment", "subsidy", "close")
EVENT_TYPES = (*CASHFLOW_TYPES, "repricing")
EVENT_FIELDS = ("deal_id", "payment_date", "event_type", "cashflow_amount",
                "repricing_value_raw", "closing_balance", "days_from_period_start", "source_line")
PAYMENT_FIELDS = ("deal_id", "payment_date", *CASHFLOW_TYPES, "payment", "closing_balance")


def normalize_events(path, version):
    content = path.read_bytes()
    lines = content.decode("utf-8-sig").splitlines()
    expected_header = "deal_id дд.мм.гггг тип сумма остаток_после дни_от_начала_периода"
    if not lines or lines[0].split() != expected_header.split():
        raise ValueError("Не совпадает заголовок TXT команды 1")
    groups = defaultdict(list)
    events = []
    seen = set()
    last_dates = {}
    counts = Counter()
    for line, text in enumerate(lines[1:], 2):
        if not text.strip():
            continue
        fields = text.split()
        if len(fields) != 6:
            raise ValueError(f"Строка {line}: ожидается шесть полей")
        deal, date_text, kind, value_text, balance_text, days_text = fields
        if kind not in EVENT_TYPES:
            raise ValueError(f"Строка {line}: неизвестный тип {kind}")
        try:
            day = datetime.strptime(date_text, "%d.%m.%Y").date()
            value, balance = Decimal(value_text), Decimal(balance_text)
            days = int(days_text)
            if any(not item.is_finite() for item in (value, balance)) or days < 0:
                raise ValueError("ожидаются конечные суммы и неотрицательное число дней")
            if balance != balance.quantize(Decimal("0.01")) or (
                kind in CASHFLOW_TYPES and value != value.quantize(Decimal("0.01"))
            ):
                raise ValueError("сумма и остаток должны иметь точность до копейки")
        except ArithmeticError as error:
            raise ValueError(f"Строка {line}: некорректное число") from error
        except ValueError as error:
            raise ValueError(f"Строка {line}: {error}") from error
        if day < last_dates.get(deal, day) or (deal, day, kind) in seen:
            raise ValueError(f"Строка {line}: нарушен порядок дат или повторяется тип события на дату")
        last_dates[deal] = day
        seen.add((deal, day, kind))
        counts[kind] += 1
        event = dict(zip(EVENT_FIELDS, (
            deal, day.isoformat(), kind, value if kind in CASHFLOW_TYPES else "",
            value if kind == "repricing" else "", balance, days, line,
        )))
        events.append(event)
        groups[deal, day].append(event)
    if not events:
        raise ValueError("В файле нет событий")
    payments, discrepancies, distinct_origins, varied_days = [], [], [], []
    previous = {}
    origins_by_stream = defaultdict(set)
    rounding = 0
    for (deal, day), rows in groups.items():
        date_key = {"deal_id": deal, "payment_date": day.isoformat()}
        balances = {row["closing_balance"] for row in rows}
        if len(balances) != 1:
            raise ValueError(f"Сделка {deal}, {day}: разные остатки на одну дату")
        balance, = balances
        amounts = dict.fromkeys(CASHFLOW_TYPES, Decimal("0.00"))
        for row in rows:
            kind = row["event_type"]
            origins_by_stream[deal, kind].add(day - timedelta(days=row["days_from_period_start"]))
            if kind in CASHFLOW_TYPES:
                amounts[kind] += row["cashflow_amount"]
        if len({row["days_from_period_start"] for row in rows}) > 1:
            varied_days.append(date_key)
        if (deal, day, "prepayment") in seen and (deal, day, "interest") not in seen:
            raise ValueError(f"Сделка {deal}, {day}: досрочное погашение без события interest")
        if deal in previous:
            principal_paid = amounts["principal"] + amounts["prepayment"] + amounts["close"]
            residual = previous[deal] - principal_paid - balance
            if abs(residual) > Decimal("0.01"):
                discrepancies.append({**date_key, "residual": str(residual)})
            elif residual:
                rounding += 1
        previous[deal] = balance
        if any(row["event_type"] in CASHFLOW_TYPES for row in rows):
            payments.append({**date_key, **amounts, "payment": sum(amounts.values()), "closing_balance": balance})
    for (deal, kind), origins in origins_by_stream.items():
        if len(origins) > 1:
            distinct_origins.append({"deal_id": deal, "event_type": kind})
    totals = {kind: str(sum(row[kind] for row in payments)) for kind in CASHFLOW_TYPES}
    report = {
        "schema_version": 1,
        "source": {"file": path.name, "version": version, "sha256": sha256(content).hexdigest()},
        "currency": None,
        "deal_count": len(last_dates),
        "event_count": len(events),
        "event_counts": dict(counts),
        "deal_date_count": len(groups),
        "payment_row_count": len(payments),
        "cashflow_totals_in_source_units": totals,
        "negative_cashflow_event_count": sum(
            row["cashflow_amount"] != "" and row["cashflow_amount"] < 0 for row in events
        ),
        "balance_pairs_checked": len(groups) - len(last_dates),
        "balance_differences_within_one_cent": rounding,
        "balance_differences_above_one_cent": discrepancies,
        "nonzero_final_balances": {deal: str(value) for deal, value in previous.items() if value},
        "different_day_counters_on_same_date": varied_days,
        "inconsistent_counter_origins_within_stream": distinct_origins,
        "prepayment_status": "Тестовые заглушки команды 1",
        "repricing_unit": None,
    }
    return events, payments, report
