from __future__ import annotations

import argparse
import csv
from datetime import date
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zipfile import BadZipFile

from openpyxl.utils.exceptions import InvalidFileException

from de_sprint3.generator import generate
from de_sprint3.team1_payments import Team1Schedule, load_team1_payments
from ml_sprint3.analysis import SCHEMA_VERSION, compare, compare_ready_payments
from ml_sprint3.curves import Curve, CurveSet, SCENARIO_ROWS, load_irrbb_workbook
from ml_sprint3.deal import DAY_COUNTS, PAYMENT_TYPES, Deal, build_schedule, finite_number, parse_date


ROOT = Path(__file__).resolve().parent.parent
CONTROL_DEAL = ROOT / "ml_sprint3/demo_deal.json"
GENERATOR_CONFIG = ROOT / "de_sprint3/default_config.json"
MAX_GENERATED_DEALS = 500
MAX_GENERATED_PERIODS = 60_000
REFERENCE = ROOT / "outputs/ml_sprint3"
WEB = Path(__file__).with_name("web")
ASSETS = {
    "/": (WEB / "index.html", "text/html; charset=utf-8"),
    "/dashboard.css": (WEB / "dashboard.css", "text/css; charset=utf-8"),
    "/dashboard.js": (WEB / "dashboard.js", "text/javascript; charset=utf-8"),
    "/charts.js": (WEB / "charts.js", "text/javascript; charset=utf-8"),
    "/generator.js": (WEB / "generator.js", "text/javascript; charset=utf-8"),
    "/reference/sprint3.csv": (REFERENCE / "journal.csv", "text/csv; charset=utf-8"),
    "/reference/sprint2.json": (ROOT / "outputs/ml_sprint2/baseline.json", "application/json"),
}
MISSING_SCENARIO = (
    "Для этого сценария нужны базовая и стрессовая кривые команды 2: "
    "дата среза, валюта, сроки и ставки с единицами измерения. "
    "Передайте подтверждённую книгу IRRBB или нормализованный JSON."
)


def load_deals(path: Path) -> list[Deal]:
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as stream:
            records = list(csv.DictReader(stream))
        for row in records:
            for field in ("principal", "annual_rate"):
                row[field] = float(row[field])
            if row.get("payment_dates"):
                row["payment_dates"] = json.loads(row["payment_dates"])
                # Keep nonempty numeric fields so the ML validator detects ambiguous calendars.
                for field in ("term_months", "payment_interval_months"):
                    if not row.get(field):
                        row.pop(field, None)
            else:
                row.pop("payment_dates", None)
                for field in ("term_months", "payment_interval_months"):
                    row[field] = int(row[field])
    elif path.suffix.lower() == ".json":
        records = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(records, dict):
            records = [records]
    else:
        raise ValueError("Передайте сделки в файле .json или .csv.")
    if not isinstance(records, list) or not records or not all(isinstance(row, dict) for row in records):
        raise ValueError("Нужен непустой набор сделок.")
    return [Deal.from_dict(row) for row in records]


def load_snapshot(path: Path, curve_date: date | None) -> tuple[CurveSet, dict]:
    snapshot = json.loads(path.read_text(encoding="utf-8-sig"))
    if snapshot["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"Нужен JSON расчёта ML с schema_version = {SCHEMA_VERSION}.")
    market = snapshot["market"]
    if market["rate_unit"] != "fraction":
        raise ValueError("Ставки кривых в JSON должны быть в долях.")
    tenors = tuple(finite_number(tenor, "tenors_years") for tenor in market["tenors_years"])
    buckets = tuple(market["bucket_labels"])
    if not tenors or tenors[0] <= 0 or any(b <= a for a, b in zip(tenors, tenors[1:])) or len(buckets) != len(tenors):
        raise ValueError("Нужны возрастающие положительные сроки и соответствующие им метки.")
    source = market["source"]
    as_of = parse_date(source["as_of"]) if source.get("as_of") else None
    if curve_date and as_of and curve_date != as_of:
        raise ValueError("Дата книги отличается от даты в JSON кривых.")
    as_of = curve_date or as_of
    source["as_of"] = as_of.isoformat() if as_of else None

    def curve(entry: dict) -> Curve:
        rates = tuple(finite_number(rate, "zero_rates") for rate in entry["zero_rates"])
        if len(rates) != len(tenors):
            raise ValueError("Для каждой кривой число ставок должно совпадать с числом сроков.")
        return Curve(entry["scenario_id"], entry["label"], rates, entry["workbook_range"])

    curves = CurveSet(
        tenors, buckets, curve(market["base_curve"]),
        tuple(map(curve, market["stress_curves"])), market["currency"], as_of, source["file"], source["sha256"],
    )
    if curves.base.scenario_id != "base" or len({item.scenario_id for item in (curves.base, *curves.stresses)}) != len(curves.stresses) + 1:
        raise ValueError("Нужна базовая кривая с ID base и уникальные ID сценариев.")
    # Show shifts derived from the actual rates used by the ML module.
    entries = (market["base_curve"], *market["stress_curves"])
    for entry, item in zip(entries, (curves.base, *curves.stresses)):
        shock = entry.get("key_rate_shock_pp")
        if shock is not None:
            finite_number(shock, "key_rate_shock_pp")
        entry["zero_rate_shifts_pp"] = curves.curve_data(item)["zero_rate_shifts_pp"]
    return curves, market


def generate_deals(config: dict) -> list[dict]:
    count = config["count"]
    if type(count) is not int or not 1 <= count <= MAX_GENERATED_DEALS:
        raise ValueError(f"За один запуск можно создать от 1 до {MAX_GENERATED_DEALS} сделок.")
    interval = config["payment_interval_months"]
    if type(interval) is not int or interval < 1:
        raise ValueError("Интервал платежей должен быть целым положительным числом месяцев.")
    longest = max(
        (int(term) for term, weight in config["term_months_weights"].items() if weight > 0),
        default=0,
    )
    if count * ((longest + interval - 1) // interval) > MAX_GENERATED_PERIODS:
        raise ValueError("Уменьшите количество сделок или максимальный срок для этого запуска.")

    deals = generate(config)
    for deal in deals:
        if deal["rate_type"] == "FIXED":
            try:
                build_schedule(Deal.from_dict(deal))
            except ValueError as error:
                raise ValueError(f"{deal['deal_id']}: не удалось построить график. {error}") from error
    return deals


class Dashboard:
    def __init__(self, deals: list[Deal], curves: CurveSet, market: dict, valuation_date: date, *,
                 team1: list[Team1Schedule] | None = None) -> None:
        self.deals = {deal.deal_id: deal for deal in deals}
        if len(self.deals) != len(deals):
            raise ValueError("ID сделок должны быть уникальными.")
        if any(deal.currency != curves.currency for deal in deals):
            raise ValueError("Валюта каждой сделки должна совпадать с валютой кривых.")
        if curves.as_of and curves.as_of != valuation_date:
            raise ValueError("Дата среза кривых должна совпадать с датой оценки.")
        self.team1 = {f"team1:{item.deal_id}": item for item in team1 or []}
        if any(item.currency != curves.currency for item in self.team1.values()):
            raise ValueError("Валюта графиков команды 1 должна совпадать с валютой кривых.")
        if self.deals.keys() & self.team1.keys():
            raise ValueError("Идентификаторы готовых графиков пересекаются с параметрами сделок.")
        self.curves = curves
        self.market = market
        self.valuation_date = valuation_date
        self.scenarios = {entry["scenario_id"]: entry for entry in (market["base_curve"], *market["stress_curves"])}

    def options(self) -> dict:
        scenarios = [
            {"id": key, "label": entry["label"], "available": True}
            for key, entry in self.scenarios.items()
        ]
        scenarios.extend(
            {"id": key, "label": label, "available": False}
            for key, (_, label) in SCENARIO_ROWS.items()
            if key not in self.scenarios
        )
        return {
            "deals": [deal.to_dict() for deal in self.deals.values()],
            "team1_deals": [item.describe(self.valuation_date) for item in self.team1.values()],
            "generator_config": json.loads(GENERATOR_CONFIG.read_text(encoding="utf-8")),
            "max_generated_deals": MAX_GENERATED_DEALS,
            "payment_types": PAYMENT_TYPES,
            "day_counts": DAY_COUNTS,
            "scenarios": scenarios,
        }

    def calculate(self, query: dict[str, list[str]], *, generated_deal: Deal | None = None) -> dict:
        deal_id, payment_type, day_count, scenario = (
            query.get(key, [""])[0] for key in ("deal", "payment_type", "day_count", "scenario")
        )
        imported = self.team1.get(deal_id) if generated_deal is None else None
        if generated_deal is None and imported is None and deal_id not in self.deals:
            raise ValueError("Выберите сделку из загруженного набора.")
        if scenario not in self.scenarios:
            raise ValueError(MISSING_SCENARIO)
        if imported is not None:
            result = compare_ready_payments(
                imported.describe(self.valuation_date), imported.rows, self.curves, self.valuation_date, scenario,
            )
            result["market"] = self.market
            return result
        if payment_type not in PAYMENT_TYPES or day_count not in DAY_COUNTS:
            raise ValueError("Выберите поддерживаемый тип платежа и базу начисления.")
        deal = generated_deal if generated_deal is not None else self.deals[deal_id]
        schedule_changed = (payment_type, day_count) != (deal.payment_type, deal.day_count)
        result = compare(
            deal, self.curves, self.valuation_date, scenario,
            reference_scenario_id=scenario if schedule_changed else "base",
            payment_type=payment_type, day_count=day_count,
        )
        # Keep source metadata supplied in the export, including any documented key-rate shock.
        result["market"] = self.market
        return result


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, dashboard: Dashboard, **kwargs) -> None:
        self.dashboard = dashboard
        super().__init__(*args, **kwargs)

    def send_content(self, body: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_content(json.dumps(data, ensure_ascii=False, allow_nan=False).encode(),
                          "application/json; charset=utf-8", status)

    def do_GET(self) -> None:
        request = urlsplit(self.path)
        if request.path in ASSETS:
            path, content_type = ASSETS[request.path]
            self.send_content(path.read_bytes(), content_type)
        elif request.path == "/api/options":
            self.send_json(self.dashboard.options())
        elif request.path == "/api/compare":
            try:
                result = self.dashboard.calculate(parse_qs(request.query))
            except (ValueError, OverflowError) as error:
                self.send_json({"error": str(error)}, HTTPStatus.UNPROCESSABLE_ENTITY)
            else:
                self.send_json(result)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        path = urlsplit(self.path).path
        if path not in ("/api/generate", "/api/compare"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            if self.headers.get_content_type() != "application/json":
                raise ValueError("Ожидаются параметры в формате JSON.")
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 16_384:
                raise ValueError("Параметры пусты или занимают слишком много места.")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("Ожидается объект с параметрами.")
            if path == "/api/generate":
                result = {"deals": generate_deals(payload)}
            else:
                deal = Deal.from_dict(payload["deal"])
                query = {key: [payload[key]] for key in ("payment_type", "day_count", "scenario")}
                result = self.dashboard.calculate(query, generated_deal=deal)
        except (ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
            self.send_json({"error": str(error)}, HTTPStatus.UNPROCESSABLE_ENTITY)
        else:
            self.send_json(result)


def main() -> None:
    parser = argparse.ArgumentParser(description="Дашборд факторного анализа для спринта 3")
    parser.add_argument("--deals", type=Path, help="Файл с одной сделкой или набором сделок в JSON/CSV")
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument("--curves", type=Path, help="Книга IRRBB команды 2 в формате XLSX")
    sources.add_argument("--market", type=Path, help="JSON результата ML с кривыми в поле market")
    parser.add_argument("--valuation-date", type=parse_date, help="Дата оценки YYYY-MM-DD")
    parser.add_argument("--curve-date", type=parse_date, help="Подтверждённая дата среза YYYY-MM-DD")
    parser.add_argument("--team1-payments", type=Path, help="Готовый payments.csv команды 1")
    parser.add_argument("--team1-currency", help="Валюта готового графика, например RUB")
    parser.add_argument("--team1-demo", action="store_true", help="Отметить учебную валюту и дату оценки")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.team1_payments and not (args.team1_currency and args.valuation_date):
        parser.error("Для готового графика укажите --team1-currency и --valuation-date.")
    valuation_date = args.valuation_date or date(2026, 11, 20)
    try:
        deals = load_deals(args.deals or CONTROL_DEAL)
        if args.curves:
            curves = load_irrbb_workbook(args.curves, args.curve_date)
            market = curves.to_dict()
        else:
            curves, market = load_snapshot(args.market or REFERENCE / "results.json", args.curve_date)
        team1 = load_team1_payments(args.team1_payments, args.team1_currency, demo=args.team1_demo) if args.team1_payments else None
        dashboard = Dashboard(deals, curves, market, valuation_date, team1=team1)
        server = ThreadingHTTPServer(("127.0.0.1", args.port), partial(Handler, dashboard=dashboard))
    except (ValueError, KeyError, TypeError, OSError, BadZipFile, InvalidFileException) as error:
        parser.error(str(error))
    with server:
        print(f"http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
