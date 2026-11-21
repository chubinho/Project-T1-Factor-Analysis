"""Подготовка входных данных спринта 3."""

import argparse
import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from ml_sprint3.deal import Deal, build_schedule
from .generator import generate


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
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise ValueError("Укажите новый каталог для результата")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=args.output.parent) as temporary:
            staging = Path(temporary) / "result"
            staging.mkdir()
            export_dataset(args.config, staging)
            staging.rename(args.output)
    except (OSError, ValueError, KeyError, TypeError, OverflowError) as error:
        parser.error(str(error))
    print(f"Готово: {args.output}")


if __name__ == "__main__":
    main()
