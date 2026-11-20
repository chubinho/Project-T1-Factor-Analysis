from __future__ import annotations

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import BadZipFile

from openpyxl.utils.exceptions import InvalidFileException

from ml_sprint2.analysis import write_csv, write_json

from .analysis import compare, demo_experiments
from .curves import SCENARIO_ROWS, load_irrbb_workbook
from .deal import DAY_COUNTS, PAYMENT_TYPES, Deal, parse_date


JOURNAL_METRICS = ("total_interest", "base_pv", "stress_pv", "dnpv", "total_principal", "final_balance")


def journal_rows(result: dict) -> list[dict]:
    rows = []
    for experiment in result["experiments"]:
        change, = experiment["changed_parameters"]
        before = result["states"][experiment["before_state"]]
        after = result["states"][experiment["after_state"]]
        row = {
            "experiment_id": experiment["experiment_id"],
            "deal_id": before["deal"]["deal_id"],
            "valuation_date": before["valuation_date"],
            "currency": before["deal"]["currency"],
            "parameter": change["parameter"],
            "before": change["before"],
            "after": change["after"],
        }
        for metric in JOURNAL_METRICS:
            row[f"before_{metric}"] = before["metrics"][metric]
            row[f"after_{metric}"] = after["metrics"][metric]
            row[f"delta_{metric}"] = experiment["delta_metrics"][metric]
        rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Sprint 3 fixed-rate cash flows and IRRBB valuation")
    subcommands = parser.add_subparsers(dest="command", required=True)
    demo = subcommands.add_parser("demo", help="Run the three separate sprint experiments")
    compare_parser = subcommands.add_parser("compare", help="Recalculate before and after selected changes")
    scenarios = ("base", *SCENARIO_ROWS)
    for command in (demo, compare_parser):
        command.add_argument("--deal", type=Path, required=True, help="Deal JSON")
        command.add_argument("--curves", type=Path, required=True, help="Team 2 IRRBB workbook")
        command.add_argument("--valuation-date", type=parse_date, required=True)
        command.add_argument("--curve-date", type=parse_date, help="Confirmed date of the workbook curve, if available")
        command.add_argument("--scenario", choices=scenarios, default="parallel_up")
        command.add_argument("--output", type=Path, required=True, help="New output directory")
    compare_parser.add_argument("--reference-scenario", choices=scenarios)
    compare_parser.add_argument("--payment-type", choices=PAYMENT_TYPES)
    compare_parser.add_argument("--day-count", choices=DAY_COUNTS)
    args = parser.parse_args()

    try:
        if args.output.exists():
            raise ValueError("Output already exists. Choose a new directory.")
        deal_data = json.loads(args.deal.read_text(encoding="utf-8-sig"))
        if not isinstance(deal_data, dict):
            raise ValueError("Deal JSON must contain an object")
        deal = Deal.from_dict(deal_data)
        curves = load_irrbb_workbook(args.curves, args.curve_date)
        if args.command == "demo":
            result = demo_experiments(deal, curves, args.valuation_date, args.scenario)
        else:
            result = compare(
                deal, curves, args.valuation_date, args.scenario,
                reference_scenario_id=args.reference_scenario,
                payment_type=args.payment_type, day_count=args.day_count,
            )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=args.output.parent, prefix=".tmp-ml-sprint3-") as temporary:
            staging = Path(temporary) / "run"
            staging.mkdir()
            if args.command == "demo":
                write_json(staging / "results.json", result)
                write_csv(staging / "journal.csv", journal_rows(result))
            else:
                write_json(staging / "comparison.json", result)
            staging.rename(args.output)
    except (ValueError, TypeError, KeyError, OSError, OverflowError, BadZipFile, InvalidFileException) as error:
        parser.error(str(error))
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
