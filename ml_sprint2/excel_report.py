from __future__ import annotations

import argparse
from datetime import date
import json
import io
import math
from pathlib import Path
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference, ScatterChart, Series
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.formatting.rule import CellIsRule
from openpyxl.utils import get_column_letter
from openpyxl.workbook.properties import CalcProperties
from openpyxl.worksheet.views import Selection

from .analysis import analyse
from .experiments import make_plan


MONEY = '#,##0.00;[Red](#,##0.00);0.00'
FAMILY = {"control": "Контроль", "principal": "Долг", "contract_rate": "Ставка", "df": "DF"}
LIMITATIONS = {
    "Single supplied stress scenario, not full EC across scenarios": "Один стрессовый сценарий. Полный ЭК по всем сценариям не рассчитывается.",
    "ACT/ACT label differs from /365 formulas": "В исходнике указано ACT/ACT, но формулы используют /365; методика сохранена.",
    "First coupon interval starts before valuation date": "Первый процентный интервал начинается до даты оценки.",
    "Nonzero final debt is preserved": "Ненулевой конечный остаток долга сохранён из исходной методики.",
    "Portfolio summary errors are outside the experiment scope": "Ошибки портфельной сводки исходника не входят в расчёт одной сделки.",
}


def _sheet(book, name, title, headers, widths, note=None):
    sheet = book.create_sheet(name)
    sheet.sheet_view.showGridLines = False
    sheet.cell(2, 2, title).font = Font(name="Arial", size=15, bold=True, color="203A55")
    if note:
        sheet.cell(3, 2, note).font = Font(name="Arial", size=10, italic=True, color="596675")
    for col, (header, width) in enumerate(zip(headers, widths), 2):
        cell = sheet.cell(5, col, header)
        cell.fill = PatternFill("solid", fgColor="203A55")
        cell.font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        sheet.column_dimensions[get_column_letter(col)].width = width
    sheet.column_dimensions["A"].width = 3
    sheet.row_dimensions[5].height = 34
    sheet.freeze_panes = "C6"
    return sheet


def _rows(sheet, rows):
    for r, values in enumerate(rows, 6):
        for c, value in enumerate(values, 2):
            cell = sheet.cell(r, c, value)
            cell.font = Font(name="Arial", size=10, color="202B36")
            cell.alignment = Alignment(vertical="center", horizontal="right" if isinstance(value, (int, float)) or (isinstance(value, str) and value.startswith("=")) else "left")
            if isinstance(value, float):
                cell.number_format = MONEY
            if r % 2 == 0:
                cell.fill = PatternFill("solid", fgColor="F1F5F9")
        sheet.row_dimensions[r].height = 22
    if rows:
        sheet.auto_filter.ref = f"B5:{get_column_letter(len(rows[0])+1)}{5+len(rows)}"


def _scatter(sheet, first, last, x_col, anchor, title, xlabel, percent=False):
    chart = ScatterChart()
    chart.title, chart.x_axis.title, chart.y_axis.title = title, xlabel, "dNPV, RUB"
    chart.style, chart.width, chart.height = 13, 23, 11
    chart.legend = None
    chart.x_axis.numFmt = "0.0%" if percent else '#,##0'
    chart.y_axis.numFmt = '#,##0'
    chart.x_axis.axPos, chart.y_axis.axPos = "b", "l"
    chart.x_axis.crosses, chart.y_axis.crosses = "min", "min"
    series = Series(Reference(sheet, min_col=8, min_row=first, max_row=last),
                    Reference(sheet, min_col=x_col, min_row=first, max_row=last), title="dNPV")
    series.marker.symbol, series.marker.size = "circle", 6
    series.graphicalProperties.line.solidFill = "176087"
    chart.series.append(series)
    sheet.add_chart(chart, anchor)


def write_excel_report(path: Path, result: dict, contract: dict, capture: dict) -> None:
    """Export a validated result with full precision; refuse to overwrite files."""
    path = Path(path)
    if path.suffix.lower() != ".xlsx":
        raise ValueError("Report path must end in .xlsx")
    if path.exists():
        raise FileExistsError(f"Report already exists: {path}")
    book = Workbook()
    book.remove(book.active)
    book.calculation = CalcProperties(calcId=0, fullCalcOnLoad=True)
    book.properties.title = "Второй спринт — однофакторные эксперименты"
    book.properties.creator = "Project T1"
    cached = {}

    def formula(sheet, row, col, expression, value):
        cell = sheet.cell(row, col, expression)
        cached.setdefault(sheet.title, {})[cell.coordinate] = value
        return cell
    summary = _sheet(book, "Сводка", "Спринт 2 · результаты ML-экспериментов",
                     ["Показатель", "Значение"], [52, 34])
    journal = _sheet(book, "Журнал", "Все однофакторные эксперименты",
                     ["ID", "Фактор", "Доп. опыт", "Ячейка", "Было", "Стало", "Долг, RUB", "Ставка",
                      "NPV базы, RUB", "NPV стресса, RUB", "dNPV, RUB", "ΔNPV базы, RUB", "ΔNPV стресса, RUB",
                      "ΔdNPV, RUB", "Вклад DF, RUB", "Платёж, RUB", "Конечный долг, RUB", "OAT проверен", "База восстановлена"],
                     [27, 14, 14, 12, 20, 20, 21, 13]+[23]*9+[18, 22],
                     "dNPV = NPV стресса − NPV базы. Δ — изменение относительно контроля, а не сам dNPV.")
    all_rows = result["journal"] + result["optional_rate_experiments"]
    optional_ids = {r["experiment_id"] for r in result["optional_rate_experiments"]}
    lookup = {r["experiment_id"]: i for i, r in enumerate(all_rows, 6)}
    _rows(journal, [[r["experiment_id"], FAMILY[r["family"]], r["experiment_id"] in optional_ids,
                     r["input_cell"], r["old_value"], r["new_value"], r["principal"], r["contract_rate"],
                     r["base_npv"], r["stress_npv"], r["dnpv"], r["delta_base_npv"], r["delta_stress_npv"],
                     r["delta_dnpv"], r["df_stress_contribution"], r["regular_payment"], r["final_balance"],
                     r["oat_verified"], r["baseline_restored"]] for r in all_rows])
    for i, r in enumerate(all_rows, 6):
        journal.cell(i, 9).number_format = "0.0%"
        if r["family"] in ("df", "contract_rate"):
            for c in (6, 7):
                journal.cell(i, c).number_format = "0.000000000" if r["family"] == "df" else "0.0%"

    sensitivity = _sheet(book, "Долг и ставка", "Чувствительность к долгу и договорной ставке",
                         ["ID", "Долг, RUB", "Ставка", "NPV базы, RUB", "NPV стресса, RUB", "Платёж, RUB", "dNPV, RUB", "ΔdNPV, RUB", "Доп. опыт"],
                         [26, 22, 14, 24, 24, 22, 24, 24, 15],
                         "В каждом опыте меняется один вход; остальные входы сохраняются. Точки ±0,5 п.п. дополнительные.")
    principal = sorted([r for r in all_rows if r["family"] in ("control", "principal")], key=lambda r: r["principal"])
    rates = sorted([r for r in all_rows if r["family"] in ("control", "contract_rate")], key=lambda r: r["contract_rate"])
    sens_rows = principal + rates
    _rows(sensitivity, [[r["experiment_id"], r["principal"], r["contract_rate"], r["base_npv"], r["stress_npv"],
                        r["regular_payment"], r["dnpv"], r["delta_dnpv"], r["experiment_id"] in optional_ids] for r in sens_rows])
    for i, r in enumerate(sens_rows, 6):
        source_row = lookup[r["experiment_id"]]
        for c, source_col, key in ((3, "H", "principal"), (4, "I", "contract_rate"), (5, "J", "base_npv"), (6, "K", "stress_npv"), (7, "Q", "regular_payment"), (8, "L", "dnpv"), (9, "O", "delta_dnpv")):
            formula(sensitivity, i, c, f"='Журнал'!{source_col}{source_row}", r[key])
            sensitivity.cell(i, c).number_format = "0.0%" if c == 4 else MONEY
    _scatter(sensitivity, 6, 5+len(principal), 3, "B18", "Влияние долга", "Долг, RUB")
    _scatter(sensitivity, 6+len(principal), 5+len(sens_rows), 4, "B41", "Влияние договорной ставки", "Ставка", True)

    df = _sheet(book, "Вклады DF", "Разложение исходного стрессового dNPV по DF",
                ["Бакет", "Ячейка базы", "DF базы", "DF стресса", "ΣCF, RUB", "Вклад в dNPV, RUB", "ΔdNPV опыта, RUB"],
                [14, 17, 20, 20, 24, 26, 26],
                "Вклад = ΣCF × (DF стресса − DF базы). В опыте меняется только DF базы: ΔdNPV имеет обратный знак.")
    bucket_map = {b["column"]: b for b in contract["buckets"]}
    df_rows = [r for r in result["journal"] if r["family"] == "df"]
    _rows(df, [[r["bucket"], r["input_cell"], r["old_value"], r["new_value"], None,
               r["df_stress_contribution"], r["delta_dnpv"]] for r in df_rows])

    flows = _sheet(book, "Денежные потоки", "Контрольный график денежных потоков",
                   ["Строка исходника", "Дата платежа", "Бакет", "CF, RUB", "DF базы", "DF стресса", "PV базы, RUB", "PV стресса, RUB", "dPV, RUB"],
                   [20, 18, 14, 23, 20, 20, 24, 24, 24],
                   "Контрольные CF пересчитаны Microsoft Excel. Распределение по бакетам сохранено из исходной книги.")
    flow_rows = []
    for f, cf in zip(contract["flows"], capture["baseline"]["cashflows"]):
        b = bucket_map[f["df_column"]]
        flow_rows.append([f["source_row"], date.fromisoformat(f["payment_date"]), b["label"], cf, b["base_df"], b["stress_df"], None, None, None])
    _rows(flows, flow_rows)
    for i in range(6, 6+len(flow_rows)):
        flows.cell(i, 3).number_format = "dd.mm.yyyy"
        for c in (6, 7):
            flows.cell(i, c).number_format = "0.000000000"
        cf, base_df, stress_df = flow_rows[i-6][3:6]
        for c, expression, value in ((8, f"=E{i}*F{i}", cf*base_df), (9, f"=E{i}*G{i}", cf*stress_df), (10, f"=I{i}-H{i}", cf*stress_df-cf*base_df)):
            formula(flows, i, c, expression, value).number_format = MONEY
    flow_end = 5+len(flow_rows)
    for i, r in enumerate(df_rows, 6):
        total_cf = math.fsum(f[3] for f in flow_rows if f[2] == r["bucket"])
        formula(df, i, 6, f'=SUMIFS(\'Денежные потоки\'!$E$6:$E${flow_end},\'Денежные потоки\'!$D$6:$D${flow_end},B{i})', total_cf).number_format = MONEY
        formula(df, i, 7, f"=F{i}*(E{i}-D{i})", total_cf*(r["new_value"]-r["old_value"])).number_format = MONEY
        formula(df, i, 8, f"='Журнал'!O{lookup[r['experiment_id']]}", r["delta_dnpv"]).number_format = MONEY
        for c in (4, 5):
            df.cell(i, c).number_format = "0.000000000"
    df_end = 5+len(df_rows)
    chart = BarChart()
    chart.title, chart.y_axis.title, chart.x_axis.title = "Вклады DF в исходный dNPV", "Вклад, RUB", "Бакет"
    chart.width, chart.height, chart.style, chart.legend = 26, 13, 13, None
    chart.add_data(Reference(df, min_col=7, min_row=5, max_row=df_end), titles_from_data=True)
    chart.set_categories(Reference(df, min_col=2, min_row=6, max_row=df_end))
    chart.y_axis.numFmt = '#,##0'
    chart.x_axis.axPos, chart.y_axis.axPos = "b", "l"
    chart.y_axis.crosses = "min"
    df.add_chart(chart, f"B{df_end+4}")

    base = result["journal"][0]
    summaries = [
        ["Дата оценки", date.fromisoformat(result["source"]["valuation_date"])],
        ["Валюта", result["source"]["currency"]], ["Остаток долга", "='Журнал'!H6"],
        ["Договорная ставка", "='Журнал'!I6"], ["NPV базы", "='Журнал'!J6"],
        ["NPV стресса", "='Журнал'!K6"], ["dNPV = стресс − база", "='Журнал'!L6"],
        ["Обязательные опыты, включая контроль", len(result["journal"])],
        ["Дополнительные опыты", len(result["optional_rate_experiments"])],
        ["Крупнейший DF-бакет по модулю", result["largest_df_effect"]["bucket"]],
        ["Вклад этого бакета, RUB", result["largest_df_effect"]["df_stress_contribution"]],
        ["Конечный остаток долга, RUB", base["final_balance"]],
    ]
    _rows(summary, summaries)
    for row, key in ((8, "principal"), (9, "contract_rate"), (10, "base_npv"), (11, "stress_npv"), (12, "dnpv")):
        cached.setdefault(summary.title, {})[f"C{row}"] = base[key]
    summary.auto_filter.ref = None
    summary.freeze_panes = None
    summary.sheet_view.selection = [Selection(activeCell="B2", sqref="B2")]
    summary["C6"].number_format = "dd.mm.yyyy"
    for r in (8, 10, 11, 12):
        summary.cell(r, 3).number_format = MONEY
    summary["C9"].number_format = "0.0%"
    notes = [
        "Сохранённый результат опытов. Изменение ячеек отчёта не пересчитывает исходную сделку.",
        "Для новых условий повторите запуск ml_sprint2; для обновления отчёта используйте excel_report.",
        "Отрицательный dNPV означает снижение NPV в выбранном стрессе; это один сценарий, а не полный ЭК.",
        "Приёмка DE и тимлида ожидается." if result["team_acceptance"] == "pending_DE_and_teamlead_review" else f"Приёмка: {result['team_acceptance']}",
    ] + [LIMITATIONS.get(v, v) for v in result["limitations"]]
    for i, note in enumerate(notes, 20):
        summary.merge_cells(start_row=i, start_column=2, end_row=i, end_column=8)
        summary.cell(i, 2, note).font = Font(name="Arial", size=10, color="596675")
        summary.cell(i, 2).alignment = Alignment(wrap_text=True, vertical="center")
        summary.row_dimensions[i].height = 30
    checks_at = 22+len(notes)
    summary.cell(checks_at, 2, "Проверки расчёта").font = Font(name="Arial", size=12, bold=True)
    checks = [
        ["Σ вкладов DF, RUB", f"=SUM('Вклады DF'!G6:G{df_end})"],
        ["Остаток: dNPV − Σ вкладов, RUB", f"=C12-C{checks_at+1}"],
        ["Допуск, RUB", result["df_balance"]["absolute_tolerance_rub"]],
        ["Баланс DF", f'=IF(ABS(C{checks_at+2})<=C{checks_at+3},"OK","ОШИБКА")'],
        ["NPV базы − ΣPV потоков, RUB", f"=C10-SUM('Денежные потоки'!H6:H{flow_end})"],
        ["NPV стресса − ΣPV потоков, RUB", f"=C11-SUM('Денежные потоки'!I6:I{flow_end})"],
        ["Один вход и восстановление базы", "Да" if result["verification"]["all_oat_and_restores_verified"] else "Нет"],
        ["Все CF проверены", "Да" if result["verification"]["all_cashflows_verified"] else "Нет"],
        ["Макс. ошибка независимой оценки, RUB", result["verification"]["max_independent_valuation_error_rub"]],
    ]
    for i, pair in enumerate(checks, checks_at+1):
        for c, value in enumerate(pair, 2):
            summary.cell(i, c, value).font = Font(name="Arial", size=10)
        summary.cell(i, 3).number_format = "0.0000000000" if i in (checks_at+2, checks_at+5, checks_at+6, checks_at+9) else MONEY
    contribution_sum = math.fsum(cached[df.title][f"G{i}"] for i in range(6, df_end+1))
    balance_residual = base["dnpv"]-contribution_sum
    for offset, value in ((1, contribution_sum), (2, balance_residual),
                          (4, "OK" if abs(balance_residual) <= result["df_balance"]["absolute_tolerance_rub"] else "ОШИБКА"),
                          (5, base["base_npv"]-math.fsum(f[3]*f[4] for f in flow_rows)),
                          (6, base["stress_npv"]-math.fsum(f[3]*f[5] for f in flow_rows))):
        cached[summary.title][f"C{checks_at+offset}"] = value
    summary.cell(checks_at+3, 3).number_format = "0.000000"
    summary.conditional_formatting.add(f"C{checks_at+4}", CellIsRule(
        operator="equal", formula=['"ОШИБКА"'], fill=PatternFill("solid", fgColor="FCE4D6"),
        font=Font(color="9C0006", bold=True)))

    source = _sheet(book, "Источник", "Происхождение данных и метод расчёта",
                    ["Поле", "Значение"], [38, 100])
    _rows(source, [
        ["Исходный файл", result["source"]["source_name"]],
        ["SHA-256 исходного файла", result["source"]["source_sha256"]],
        ["Лист исходника", contract.get("sheet", "1 сделка")],
        ["Ячейки долга / ставки", "F3 / J3"], ["Ячейки NPV базы / стресса / dNPV", "K353 / M353 / N353"],
        ["Количество платежей", contract["payments_count"]],
        ["Движок пересчёта", result["engine"]], ["Версия Excel", result["excel_version"]],
        ["Первичные результаты", "raw_capture.json; source_contract.json; experiment_plan.json"],
        ["Ставки", "Ставка в журнале — договорная; DF базы и стресса взяты из исходной книги."],
        ["OAT", "Один фактор за опыт; перед следующим опытом восстанавливается контрольная база."],
        ["Детализация", "Контрольные CF на листе «Денежные потоки»; CF всех опытов в raw_capture.json."],
    ])
    book.move_sheet(sensitivity, offset=-1)
    book.move_sheet(df, offset=-1)
    summary.sheet_properties.tabColor = "203A55"
    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    book.save(buffer)
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    parts = {f"xl/worksheets/sheet{i}.xml": cached.get(sheet.title, {})
             for i, sheet in enumerate(book.worksheets, 1)}
    with path.open("xb") as handle, ZipFile(buffer) as source, ZipFile(handle, "w") as target:
        for part in source.infolist():
            content = source.read(part.filename)
            if parts.get(part.filename):
                root = ET.fromstring(content)
                for cell in root.iter(f"{ns}c"):
                    address = cell.get("r")
                    if address in parts[part.filename]:
                        value = parts[part.filename][address]
                        cell.set("t", "str" if isinstance(value, str) else "n")
                        node = cell.find(f"{ns}v")
                        if node is None:
                            node = ET.SubElement(cell, f"{ns}v")
                        node.text = str(value)
                content = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            target.writestr(part, content)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export verified saved ML experiments to an Excel report")
    parser.add_argument("--input", required=True, type=Path, help="Directory with source_contract.json and raw_capture.json")
    parser.add_argument("--output", required=True, type=Path, help="New .xlsx report; existing files are never overwritten")
    args = parser.parse_args()
    contract = json.loads((args.input / "source_contract.json").read_text(encoding="utf-8-sig"))
    capture = json.loads((args.input / "raw_capture.json").read_text(encoding="utf-8-sig"))
    required = make_plan(contract)
    half_point = len(capture["experiments"]) != len(required)
    result = analyse(contract, make_plan(contract, half_point), capture)
    write_excel_report(args.output, result, contract, capture)
    print(f"Excel report: {args.output.resolve()}")


if __name__ == "__main__":
    main()
