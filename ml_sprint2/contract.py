from __future__ import annotations

import hashlib
from datetime import datetime
import math
from pathlib import Path
import re

from openpyxl import load_workbook

SHEET = "1 сделка"
DF_COLUMNS = ("B", "C", "D", "E", "F", "G", "I", "J", "K", "L", "M", "N", "O", "P", "Q", "R", "S", "T", "U")
FLOW_ROWS = range(19, 353)
MONEY_TOLERANCE = 1e-6  


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label}: expected a finite number, got {value!r}")
    return float(value)


def normalize_formula(value: object) -> str:
    return str(value).replace(" ", "").upper()


def read_contract(source: Path) -> dict:
    source = source.resolve(strict=True)
    if source.suffix.lower() != ".xlsx":
        raise ValueError("Expected an .xlsx calculation workbook")
    digest = sha256(source)
    formulas = load_workbook(source, data_only=False)
    cached = load_workbook(source, data_only=True)
    try:
        if SHEET not in formulas:
            raise ValueError(f"Required sheet {SHEET!r} is missing; this is not the sprint 2 workbook")
        s, c = formulas[SHEET], cached[SHEET]
        if formulas._external_links:
            raise ValueError("External workbook links require a separately agreed DE contract")
        if c["B3"].value != "ANN" or c["I3"].value != "FIXED" or c["D3"].value != "RUB":
            raise ValueError("This experiment contract supports the provided fixed-rate ANN in RUB")
        principal = number(c["F3"].value, "F3")
        rate = number(c["J3"].value, "J3")
        if principal <= 0 or not math.isclose(rate, .075, abs_tol=1e-12, rel_tol=0):
            raise ValueError("The sprint plan requires positive opening principal and 7.5% baseline rate")
        if c["B15"].value != len(FLOW_ROWS):
            raise ValueError("Expected 334 payments in rows 19:352")
        expected_outputs = {"K353": "=SUM(J19:J352)", "M353": "=SUM(L19:L352)", "N353": "=M353-K353"}
        for address, expected in expected_outputs.items():
            if normalize_formula(s[address].value) != expected:
                raise ValueError(f"{address}: output formula changed; update the contract with DE")
        if not isinstance(c["A3"].value, datetime):
            raise ValueError("A3 must contain an Excel valuation date")
        buckets = []
        for col in DF_COLUMNS:
            base, stress = f"{col}9", f"{col}11"
            if s[base].data_type == "f" or s[stress].data_type == "f":
                raise ValueError(f"{base}/{stress}: DF controls must be independent numeric inputs")
            b = number(c[base].value, base)
            st = number(c[stress].value, stress)
            if min(b, st) <= 0:
                raise ValueError("DF must be positive; values above 1 are allowed")
            buckets.append({"column": col, "label": str(c[f"{col}5"].value), "base_cell": base,
                            "stress_cell": stress, "base_df": b, "stress_df": st})
        for address in ("F3", "J3"):
            if s[address].data_type == "f":
                raise ValueError(f"{address}: expected an independent input, not a formula")
        if s["H9"].value is not None or s["H11"].value is not None:
            raise ValueError("Unexpected DF in skipped H column")
        labels = [b["label"] for b in buckets]
        if len(set(labels)) != 19 or "None" in labels:
            raise ValueError("DF bucket labels must be present and unique")
        rows = []
        for row in FLOW_ROWS:
            base_match = re.fullmatch(rf"=G{row}\*\$([A-Z]+)\$9", normalize_formula(s[f"J{row}"].value))
            stress_match = re.fullmatch(rf"=G{row}\*\$([A-Z]+)\$11", normalize_formula(s[f"L{row}"].value))
            if not base_match or not stress_match or base_match[1] != stress_match[1] or base_match[1] not in DF_COLUMNS:
                raise ValueError(f"Row {row}: expected matching direct CF * bucket DF formulas")
            if normalize_formula(s[f"D{row}"].value) != f"=$B$14-F{row}" or normalize_formula(s[f"G{row}"].value) != f"=SUM(D{row},F{row})":
                raise ValueError(f"Row {row}: payment identity changed; do not use the independent annuity check")
            date = c[f"B{row}"].value
            if not hasattr(date, "isoformat"):
                raise ValueError(f"B{row}: missing payment date")
            rows.append({"source_row": row, "payment_date": date.date().isoformat(),
                         "df_column": base_match[1], "cached_cashflow": number(c[f"G{row}"].value, f"G{row}")})
        cached_outputs = {key: number(c[address].value, address) for key, address in
                          (("base_npv", "K353"), ("stress_npv", "M353"), ("dnpv", "N353"))}
        if sha256(source) != digest:
            raise ValueError("Source changed while the input contract was read")
        return {"schema_version": 1, "source_name": source.name, "source_sha256": digest,
                "sheet": SHEET, "used_range": s.calculate_dimension(),
                "valuation_date": c["A3"].value.date().isoformat(), "currency": "RUB",
                "principal": principal, "contract_rate": rate, "payments_count": len(rows),
                "buckets": buckets, "flows": rows, "cached_outputs": cached_outputs,
                "cached_final_balance": number(c["E352"].value, "E352"),
                "annuity_formula": s["B14"].value,
                "team_acceptance": "pending_DE_and_teamlead_review",
                "limitations": ["Single supplied stress scenario, not full EC across scenarios",
                                "ACT/ACT label differs from /365 formulas",
                                "First coupon interval starts before valuation date",
                                "Nonzero final debt is preserved",
                                "Portfolio summary errors are outside the experiment scope"]}
    finally:
        formulas.close()
        cached.close()
