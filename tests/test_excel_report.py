from pathlib import Path
import tempfile
import unittest

from openpyxl import load_workbook

from ml_sprint2.analysis import analyse
from ml_sprint2.excel_report import write_excel_report
from test_ml_sprint2 import synthetic_capture


class ExcelReportTests(unittest.TestCase):
    def test_evidence_and_links_with_and_without_optional_trials(self):
        for half_point in (False, True):
            with self.subTest(half_point=half_point), tempfile.TemporaryDirectory() as folder:
                contract, plan, capture = synthetic_capture(half_point)
                result = analyse(contract, plan, capture)
                path = Path(folder) / "report.xlsx"
                write_excel_report(path, result, contract, capture)
                book = load_workbook(path)
                self.assertEqual(book.sheetnames, ["Сводка", "Долг и ставка", "Вклады DF", "Журнал", "Денежные потоки", "Источник"])
                rows = list(book["Журнал"].iter_rows(min_row=6, values_only=True))
                self.assertEqual(len(rows), 26 if half_point else 24)
                self.assertEqual(sum(row[3] is True for row in rows), 2 if half_point else 0)
                self.assertAlmostEqual(book["Журнал"]["J6"].value, capture["baseline"]["base_npv"], places=8)
                self.assertAlmostEqual(book["Журнал"]["L6"].value, capture["baseline"]["dnpv"], places=8)
                self.assertEqual(book["Долг и ставка"]["H6"].value, "='Журнал'!L7")
                self.assertEqual(book["Вклады DF"]["G6"].value, "=F6*(E6-D6)")
                self.assertEqual(book["Вклады DF"]["H6"].value, "='Журнал'!O11")
                self.assertEqual(book["Денежные потоки"].max_row, 339)
                self.assertAlmostEqual(book["Денежные потоки"]["E6"].value, capture["baseline"]["cashflows"][0], places=8)
                self.assertEqual(book["Источник"]["C7"].value, contract["source_sha256"])
                self.assertEqual(book["Долг и ставка"]["D6"].number_format, "0.0%")
                self.assertIsNone(book["Сводка"].sheet_view.pane)
                self.assertTrue(all(s.pane is None for s in book["Сводка"].sheet_view.selection))
                values = load_workbook(path, data_only=True)
                self.assertAlmostEqual(values["Сводка"]["C12"].value, result["journal"][0]["dnpv"], places=8)
                for sheet in book:
                    for row in sheet:
                        for cell in row:
                            if cell.data_type == "f":
                                self.assertIsNotNone(values[sheet.title][cell.coordinate].value)
                # A zero-CF bucket must stay a real numeric zero, not a missing value.
                self.assertEqual(values["Вклады DF"]["G6"].value, 0)
                values.close()
                book.close()

    def test_existing_report_is_preserved(self):
        contract, plan, capture = synthetic_capture()
        result = analyse(contract, plan, capture)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "report.xlsx"
            path.write_bytes(b"existing report")
            with self.assertRaises(FileExistsError):
                write_excel_report(path, result, contract, capture)
            self.assertEqual(path.read_bytes(), b"existing report")


if __name__ == "__main__":
    unittest.main()
