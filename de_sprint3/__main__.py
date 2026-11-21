"""Подготовка входных данных спринта 3."""

import argparse
import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import BadZipFile

from openpyxl.utils.exceptions import InvalidFileException

from ml_sprint3.analysis import SCHEMA_VERSION
from ml_sprint3.curves import load_irrbb_workbook
from ml_sprint3.deal import Deal, build_schedule, parse_date
from .generator import generate
from .team1 import normalize_schedule
from .team1_events import EVENT_FIELDS, PAYMENT_FIELDS, normalize_events


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_csv(path, fields, rows):
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def export_dataset(config_path, output):
    config = json.loads(config_path.read_text(encoding="utf-8"))
    demo_path = Path(__file__).resolve().parents[1] / "ml_sprint3/demo_deal.json"
    demo = json.loads(demo_path.read_text(encoding="utf-8"))
    rows = [demo, *generate(config)]
    fields = list(demo)
    fixed = [row for row in rows if row["rate_type"] == "FIXED"]
    # Проверяем, что каждая фиксированная сделка строит график в модуле ML.
    for row in fixed:
        build_schedule(Deal.from_dict(row))
    if len({row["deal_id"] for row in rows}) != len(rows):
        raise ValueError("В наборе повторяются идентификаторы сделок")
    (output / "demo_deal.json").write_bytes(demo_path.read_bytes())
    write_csv(output / "deals_fixed.csv", fields, fixed)
    write_csv(output / "deals_all.csv", fields + ["rate_index", "rate_spread", "reset_interval_months"], rows)
    write_json(output / "manifest.json", {
        "schema_version": 1,
        "demo_deal_id": demo["deal_id"],
        "demo_source": "ml_sprint3/demo_deal.json",
        "total_deals": len(rows),
        "fixed_deals": len(fixed),
        "floating_deals": len(rows) - len(fixed),
        "config": config,
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    dataset = commands.add_parser("generate", help="Выгрузить тестовые сделки")
    dataset.add_argument("--config", type=Path, default=Path(__file__).with_name("default_config.json"))
    dataset.add_argument("--output", type=Path, required=True)
    curves = commands.add_parser("curves", help="Проверить и нормализовать сценарии IRRBB")
    curves.add_argument("--source", type=Path, required=True)
    curves.add_argument("--valuation-date", type=parse_date, required=True)
    curves.add_argument("--curve-date", type=parse_date, help="Подтверждённая дата кривой")
    curves.add_argument("--output", type=Path, required=True)
    team1 = commands.add_parser("team1", help="Проверить и нормализовать CSV графика")
    team1.add_argument("--source", type=Path, required=True)
    team1.add_argument("--source-version", required=True)
    team1.add_argument("--deal-id", required=True)
    team1.add_argument("--currency", default="RUB")
    team1.add_argument("--start-date", type=parse_date, required=True)
    team1.add_argument("--output", type=Path, required=True)
    events = commands.add_parser("team1-events", help="Загрузить общий TXT графиков команды 1")
    events.add_argument("--source", type=Path, required=True)
    events.add_argument("--source-version", required=True)
    events.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise ValueError("Укажите новый каталог для результата")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=args.output.parent) as temporary:
            staging = Path(temporary) / "result"
            staging.mkdir()
            if args.command == "generate":
                export_dataset(args.config, staging)
            elif args.command == "curves":
                if args.curve_date and args.curve_date != args.valuation_date:
                    raise ValueError("Подтверждённая дата кривой должна совпадать с датой оценки")
                market = load_irrbb_workbook(args.source, args.curve_date).to_dict()
                write_json(staging / "market.json", {"schema_version": SCHEMA_VERSION, "market": market})
            elif args.command == "team1-events":
                events, payments, report = normalize_events(args.source, args.source_version)
                write_csv(staging / "events.csv", EVENT_FIELDS, events)
                write_csv(staging / "payments.csv", PAYMENT_FIELDS, payments)
                write_json(staging / "validation.json", report)
            else:
                schedule = normalize_schedule(args.source, args.deal_id, args.currency,
                                              args.source_version, args.start_date)
                write_json(staging / "schedule.json", schedule)
            staging.rename(args.output)
    except (OSError, ValueError, KeyError, TypeError, OverflowError, BadZipFile, InvalidFileException) as error:
        parser.error(str(error))
    print(f"Готово: {args.output}")
    if args.command == "team1-events" and report["balance_differences_above_one_cent"]:
        parser.exit(1, "Обнаружены расхождения остатков больше 0,01. См. validation.json.\n")


if __name__ == "__main__":
    main()
