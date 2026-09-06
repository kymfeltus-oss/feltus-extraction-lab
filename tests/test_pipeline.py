from __future__ import annotations

import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from app.config import Settings
from app.db import Database
from app.service import IngestionService
from app.interpreter import interpret_bank_statement


def _tesseract_available() -> bool:
    if shutil.which("tesseract"):
        return True
    if os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Tesseract-OCR" / "tesseract.exe"
        return candidate.is_file()
    return False


def make_pdf(lines: list[str]) -> bytes:
    buffer = io.BytesIO()
    canvas = Canvas(buffer)
    y = 760
    for line in lines:
        canvas.drawString(70, y, line)
        y -= 22
    canvas.save()
    return buffer.getvalue()


def make_multipage_pdf(pages: list[list[str]]) -> bytes:
    buffer = io.BytesIO()
    canvas = Canvas(buffer)
    for lines in pages:
        y = 760
        for line in lines:
            canvas.drawString(70, y, line)
            y -= 22
        canvas.showPage()
    canvas.save()
    return buffer.getvalue()


def _get_test_font():
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\calibri.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
    ]
    for path in paths:
        try:
            return ImageFont.truetype(path, 40)
        except OSError:
            continue
    return ImageFont.load_default()


def make_scanned_pdf(lines: list[str]) -> bytes:
    image = Image.new("RGB", (1700, 2200), "white")
    draw = ImageDraw.Draw(image)
    font = _get_test_font()
    y = 150
    for line in lines:
        draw.text((140, y), line, fill="black", font=font)
        y += 75
    image_bytes = io.BytesIO()
    image.save(image_bytes, format="PNG")
    image_bytes.seek(0)
    pdf = io.BytesIO()
    canvas = Canvas(pdf)
    canvas.drawImage(ImageReader(image_bytes), 0, 0, width=595, height=770)
    canvas.save()
    return pdf.getvalue()


class PipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        data = Path(self.temp.name)
        self.settings = Settings(data, data, data / "lab.sqlite3", data / "uploads", 5 * 1024 * 1024, 150, "eng", None, None)
        self.db = Database(self.settings.database_path)
        self.db.initialize()
        self.service = IngestionService(self.settings, self.db)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def ingest(self, lines: list[str], filename: str) -> dict:
        document = self.service.store_upload(io.BytesIO(make_pdf(lines)), filename, "application/pdf")
        run_id = self.service.process(document)
        return self.db.get_run(run_id)

    def test_bank_statement_is_extracted_and_persisted(self) -> None:
        run = self.ingest([
            "Chase Bank", "Bank Statement", "Account ending 4821",
            "Statement Period: 01/01/2026 through 01/31/2026",
            "Beginning Balance $8,455.22", "01/03 Payroll Deposit 5,800.00",
            "01/08 Zelle Payment 1,250.00", "Ending Balance $13,005.22",
        ], "Chase_January_2026.pdf")
        self.assertEqual(run["document_type"], "PDF_DOCUMENT")
        self.assertNotIn("account", run["normalized"])
        self.assertNotIn("transactions", run["normalized"])
        self.assertEqual(len(run["pages"]), 1)
        self.assertTrue(run["pages"][0]["lines"])
        self.assertTrue(run["pages"][0]["lines"][0]["tokens"])

    def test_pay_stub_is_extracted_and_persisted(self) -> None:
        run = self.ingest([
            "Earnings Statement", "Employer: ABC Corporation", "Employee: Jane Smith",
            "Pay Period: 01/01/2026 through 01/15/2026", "Pay Date: 01/20/2026",
            "Gross Pay: $4,750.00", "Federal Tax: $620.00", "Social Security: $294.50",
            "Medicare: $68.88", "Net Pay: $3,581.62",
        ], "Jane_Smith_Pay_Stub.pdf")
        self.assertEqual(run["document_type"], "PDF_DOCUMENT")
        self.assertNotIn("pay_date", run["normalized"])
        self.assertIn("Pay Date: 01/20/2026", run["raw_text"])

    def test_rejects_non_pdf_content(self) -> None:
        with self.assertRaisesRegex(ValueError, "not a PDF"):
            self.service.store_upload(io.BytesIO(b"hello"), "fake.pdf", "application/pdf")

    def test_preserves_safe_folder_relative_path(self) -> None:
        payload = make_pdf(["Folder upload evidence"])
        document = self.service.store_upload(
            io.BytesIO(payload), "statement.pdf", "application/pdf",
            "Client A/Bank Statements/2026/statement.pdf",
        )
        run = self.db.get_run(self.service.process(document))
        self.assertEqual(run["source_relative_path"], "Client A/Bank Statements/2026/statement.pdf")
        self.assertEqual(self.db.list_runs()[0]["source_relative_path"], "Client A/Bank Statements/2026/statement.pdf")

    def test_rejects_unsafe_folder_relative_path(self) -> None:
        payload = make_pdf(["Path safety evidence"])
        document = self.service.store_upload(
            io.BytesIO(payload), "statement.pdf", "application/pdf",
            "../../outside/statement.pdf",
        )
        self.assertEqual(document["source_relative_path"], "statement.pdf")

    def test_enforces_configured_per_file_size_limit(self) -> None:
        limited_settings = Settings(
            self.settings.root_dir, self.settings.data_dir, self.settings.database_path,
            self.settings.upload_dir, 100, self.settings.ocr_dpi, self.settings.ocr_language,
            None, None,
        )
        limited_service = IngestionService(limited_settings, self.db)
        with self.assertRaisesRegex(ValueError, "exceeds"):
            limited_service.store_upload(io.BytesIO(make_pdf(["Oversized evidence"])), "large.pdf", "application/pdf")

    def test_rerun_is_immutable(self) -> None:
        payload = make_pdf(["Bank Statement", "Beginning Balance $1.00", "Ending Balance $1.00"])
        document = self.service.store_upload(io.BytesIO(payload), "statement.pdf", "application/pdf")
        first = self.service.process(document)
        second = self.service.process(document)
        self.assertNotEqual(first, second)
        self.assertEqual(len(self.db.list_runs()), 2)

    def test_generic_pdf_extracts_every_page_without_financial_warnings(self) -> None:
        payload = make_multipage_pdf([
            ["LEGAL RECORD PAGE ONE", "Cause number 2026-CV-101"],
            ["LEGAL RECORD PAGE TWO", "Interrogatory response"],
            ["LEGAL RECORD PAGE THREE", "Certificate of service"],
        ])
        document = self.service.store_upload(io.BytesIO(payload), "legal-record.pdf", "application/pdf")
        run = self.db.get_run(self.service.process(document))
        self.assertEqual(run["page_count"], 3)
        self.assertEqual(run["normalized"]["source_extraction"]["pages_with_text"], 3)
        self.assertEqual(len(run["normalized"]["source_extraction"]["page_results"]), 3)
        self.assertIn("--- PAGE 1 ---", run["raw_text"])
        self.assertIn("--- PAGE 2 ---", run["raw_text"])
        self.assertIn("--- PAGE 3 ---", run["raw_text"])
        self.assertFalse(any("balance" in warning.lower() for warning in run["warnings"]))
        self.assertEqual(run["status"], "VALIDATED")

    @unittest.skipUnless(_tesseract_available(), "Tesseract is not installed")
    def test_scanned_pdf_uses_real_ocr_fallback(self) -> None:
        payload = make_scanned_pdf([
            "BANK STATEMENT", "Account ending 9911", "Statement Period 03/01/2026 through 03/31/2026",
            "Beginning Balance $900.00", "Ending Balance $900.00",
        ])
        document = self.service.store_upload(io.BytesIO(payload), "scanned-bank-statement.pdf", "application/pdf")
        run = self.db.get_run(self.service.process(document))
        self.assertTrue(run["ocr_used"])
        self.assertEqual(run["document_type"], "PDF_DOCUMENT")
        self.assertEqual(run["pages"][0]["method"], "SCANNED_OCR")
        self.assertTrue(run["pages"][0]["lines"][0]["tokens"][0]["bbox"])

    def test_step2_multiline_statement_reconciles_from_step1_contract(self) -> None:
        payload = make_multipage_pdf([
            ["Bank of America", "Adv Plus Banking", "Account number: 3830",
             "Beginning balance on January 1, 2026 $1,000.00",
             "Deposits and other additions $700.00",
             "Withdrawals and other subtractions -$400.00",
             "Ending balance on January 31, 2026 $1,300.00"],
            ["Deposits and other additions",
             "01/03/26 ACME DES:PAYROLL 500.00",
             "01/04/26 Zelle payment from Jane Smith 200.00",
             "Total deposits and other additions $700.00",
             "Withdrawals and other subtractions",
             "01/05/26 Zelle payment to Store -300.00",
             "01/06/26 Online transfer to CHK",
             "7150 Confirmation abc123 -100.00",
             "Total withdrawals and other subtractions -$400.00"],
            ["Important account information"], ["Fee schedule"], ["Privacy notice"], ["End of statement"],
        ])
        document = self.service.store_upload(io.BytesIO(payload), "boa-personal-3830.pdf", "application/pdf")
        run = self.db.get_run(self.service.process(document))
        result = interpret_bank_statement(run)
        self.assertEqual(result["institution"]["name"], "Bank of America, N.A.")
        self.assertEqual(result["account"]["last4"], "3830")
        self.assertEqual(result["account"]["account_type"], "PERSONAL_CHECKING")
        self.assertEqual(len(result["transactions"]), 4)
        self.assertIn("7150 Confirmation abc123", result["transactions"][3]["description"])
        self.assertEqual(result["reconciliation"]["status"], "VERIFIED")
        self.assertEqual(result["reconciliation"]["balance_variance"], "0.00")

    def test_step2_never_passes_missing_controls_or_conflicting_direction(self) -> None:
        payload = make_multipage_pdf([
            ["Bank of America", "Account number: 4444"],
            ["Deposits and other additions", "01/03/26 Contradictory transaction -25.00"],
        ])
        document = self.service.store_upload(io.BytesIO(payload), "incomplete-statement.pdf", "application/pdf")
        run = self.db.get_run(self.service.process(document))
        result = interpret_bank_statement(run)
        self.assertEqual(result["transactions"][0]["direction"], "UNRESOLVED_DIRECTION")
        self.assertEqual(result["reconciliation"]["status"], "INCOMPLETE")
        self.assertFalse(result["reconciliation"]["controls_complete"])


if __name__ == "__main__":
    unittest.main()
