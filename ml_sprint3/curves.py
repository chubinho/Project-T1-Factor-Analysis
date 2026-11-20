"""Read published IRRBB scenario rates and match them to payment tenors."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
from io import BytesIO
import math
from pathlib import Path

from openpyxl import load_workbook

from .deal import finite_number


SCENARIO_ROWS = {
    "parallel_up": (36, "parallel up"),
    "parallel_down": (39, "parallel down"),
    "steepener": (42, "steepener"),
    "flattener": (45, "flattener"),
    "short_up": (48, "short rate up"),
    "short_down": (51, "short rate down"),
}

DISCOUNTING = {
    "year_fraction": "ACT/365 from valuation date",
    "formula": "DF = exp(-zero_rate * tenor_years)",
    "interpolation": "linear on zero rates between the supplied tenors",
    "outside_grid": "constant nearest zero rate",
    "included_flows": "payment_date > valuation_date",
}


def interpolate(tenors: tuple[float, ...], rates: tuple[float, ...], tenor: float) -> float:
    if tenor <= tenors[0]:
        return rates[0]
    if tenor >= tenors[-1]:
        return rates[-1]
    right = bisect_right(tenors, tenor)
    left = right - 1
    weight = (tenor - tenors[left]) / (tenors[right] - tenors[left])
    return rates[left] + weight * (rates[right] - rates[left])


@dataclass(frozen=True)
class Curve:
    scenario_id: str
    label: str
    rates: tuple[float, ...]
    workbook_range: str


@dataclass(frozen=True)
class CurveSet:
    tenors: tuple[float, ...]
    bucket_labels: tuple[str, ...]
    base: Curve
    stresses: tuple[Curve, ...]
    currency: str
    as_of: date | None
    source_file: str
    source_sha256: str

    def scenario(self, scenario_id: str) -> Curve:
        for curve in (self.base, *self.stresses):
            if curve.scenario_id == scenario_id:
                return curve
        choices = ", ".join(curve.scenario_id for curve in (self.base, *self.stresses))
        raise ValueError(f"Unknown scenario {scenario_id!r}. Available: {choices}")

    def rate(self, curve: Curve, tenor: float) -> float:
        return interpolate(self.tenors, curve.rates, tenor)

    def to_dict(self) -> dict:
        return {
            "currency": self.currency,
            "source": {
                "team": 2,
                "file": self.source_file,
                "sha256": self.source_sha256,
                "sheet": "IRRBB",
                "as_of": self.as_of.isoformat() if self.as_of else None,
                "tenor_range": "B25:T25",
                "bucket_label_range": "B24:T24",
                "input_rate_unit": "percent",
                "key_rate_link_documented": False,
            },
            "tenors_years": list(self.tenors),
            "bucket_labels": list(self.bucket_labels),
            "rate_unit": "fraction",
            "base_curve": self.curve_data(self.base),
            "stress_curves": [self.curve_data(curve) for curve in self.stresses],
            "discounting": DISCOUNTING.copy(),
        }

    def curve_data(self, curve: Curve) -> dict:
        return {
            "scenario_id": curve.scenario_id,
            "label": curve.label,
            "zero_rates": list(curve.rates),
            "zero_rate_shifts_pp": [100 * (rate - base) for rate, base in zip(curve.rates, self.base.rates)],
            "key_rate_shock_pp": None,
            "workbook_range": curve.workbook_range,
        }


def load_irrbb_workbook(path: Path, as_of: date | None = None) -> CurveSet:
    """Load numeric cached values from the Team 2 workbook without modifying it."""
    if path.suffix.lower() != ".xlsx":
        raise ValueError("Expected the Team 2 .xlsx workbook")
    content = path.read_bytes()
    book = load_workbook(BytesIO(content), read_only=True, data_only=True, keep_links=False)
    try:
        if "IRRBB" not in book:
            raise ValueError("Missing IRRBB sheet")
        sheet = book["IRRBB"]
        # Access once so openpyxl can stream the worksheet.
        rows = dict(enumerate(sheet.iter_rows(max_row=51, max_col=20, values_only=True), 1))

        def numbers(row: int) -> tuple[float, ...]:
            values = rows[row][1:]
            if len(values) != 19:
                raise ValueError(f"IRRBB row {row}: expected 19 tenors")
            return tuple(finite_number(value, f"IRRBB row {row}, column {col}")
                         for col, value in enumerate(values, 2))

        if numbers(23) != tuple(range(1, 20)):
            raise ValueError("IRRBB B23:T23: expected bucket IDs 1 to 19")
        labels = rows[24][1:]
        if any(not isinstance(label, str) or not label.strip() for label in labels) or len(set(labels)) != 19:
            raise ValueError("IRRBB B24:T24: expected unique bucket labels")
        tenors = numbers(25)
        if tenors[0] <= 0 or any(right <= left for left, right in zip(tenors, tenors[1:])):
            raise ValueError("IRRBB B25:T25: expected strictly increasing positive tenors in years")
        if rows[30][0] != "Базовая кривая G-curve, %":
            raise ValueError("IRRBB A30: expected the base curve in percent")

        base_values = numbers(30)
        floor_values = numbers(29)
        base = Curve("base", "Базовая кривая G-curve", tuple(rate / 100 for rate in base_values), "B30:T30")
        stresses = []
        for scenario, (row, expected_name) in SCENARIO_ROWS.items():
            label = rows[row - 2][0]
            header = rows[row][0]
            if not isinstance(label, str) or expected_name not in label.casefold():
                raise ValueError(f"IRRBB row {row - 2}: expected scenario {expected_name}")
            if not isinstance(header, str) or header.strip() != "Кривая под стрессом, %":
                raise ValueError(f"IRRBB row {row}: expected a stress curve in percent")
            values = numbers(row)
            for index, (value, base_rate, shock, floor) in enumerate(
                zip(values, base_values, numbers(row - 1), floor_values), 1
            ):
                expected = base_rate + shock
                if shock < 0:
                    expected = max(expected, min(base_rate, floor))
                if not math.isclose(value, expected, abs_tol=1e-10, rel_tol=0):
                    raise ValueError(f"IRRBB row {row}, bucket {index}: stress value differs from base + shock with floor")
            stresses.append(Curve(scenario, label, tuple(rate / 100 for rate in values), f"B{row}:T{row}"))
    finally:
        book.close()
    return CurveSet(tenors, labels, base, tuple(stresses), "RUB", as_of, path.name, sha256(content).hexdigest())
