"""Проверка согласованного формата графика команды 1."""

import csv
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from io import StringIO

from ml_sprint3.deal import parse_date


REQUIRED_FIELDS = ("deal_id", "currency", "payment_date", "principal", "interest", "payment")
OPTIONAL_FIELDS = ("prepayment", "subsidy")


def normalize_schedule(path, deal_id, currency, source_version, start_date):
    content = path.read_bytes()
    reader = csv.DictReader(StringIO(content.decode("utf-8-sig"), newline=""))
    headers = reader.fieldnames or []
    if not set(REQUIRED_FIELDS).issubset(headers) or len(headers) != len(set(headers)):
        raise ValueError("В графике отсутствуют обязательные поля или повторяются заголовки")
    if set(headers) - set(REQUIRED_FIELDS + OPTIONAL_FIELDS):
        raise ValueError("В графике есть поля вне согласованного формата")
    previous_date = start_date
    schedule = []
    for row in reader:
        line = reader.line_num
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f"Строка {line}: число значений не совпадает с заголовком")
        if row["deal_id"] != deal_id or row["currency"] != currency:
            raise ValueError(f"Строка {line}: неверный ID сделки или валюта")
        payment_date = parse_date(row["payment_date"])
        if payment_date <= previous_date:
            raise ValueError(f"Строка {line}: даты должны строго возрастать после начала начисления")
        amounts = {}
        for field in ("principal", "interest", "payment", *OPTIONAL_FIELDS):
            try:
                value = Decimal(row.get(field, "0"))
                if not value.is_finite() or value < 0 or value != value.quantize(Decimal("0.01")):
                    raise InvalidOperation
            except InvalidOperation as error:
                raise ValueError(f"Строка {line}: {field} должно быть неотрицательной суммой с точностью до копейки") from error
            amounts[field] = value
        if amounts["payment"] != sum(amounts[field] for field in ("principal", "interest", *OPTIONAL_FIELDS)):
            raise ValueError(f"Строка {line}: платёж не равен сумме компонентов")
        schedule.append({
            "payment_date": payment_date.isoformat(),
            **{field: float(value) for field, value in amounts.items()},
        })
        previous_date = payment_date
    if not schedule:
        raise ValueError("График пуст")
    return {
        "schema_version": 1,
        "source": {"file": path.name, "sha256": sha256(content).hexdigest(), "version": source_version},
        "deal_id": deal_id,
        "currency": currency,
        "start_date": start_date.isoformat(),
        "schedule": schedule,
    }
