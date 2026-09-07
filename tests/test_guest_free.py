from __future__ import annotations

import io
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from reportlab.pdfgen.canvas import Canvas

from app.db import Database
from app.guest_access import GuestExtractionLedger
from app.main import app


def make_pdf(page_count: int = 1) -> bytes:
    output = io.BytesIO()
    canvas = Canvas(output)
    for page in range(page_count):
        canvas.drawString(72, 720, f"Guest extraction page {page + 1}")
        canvas.showPage()
    canvas.save()
    return output.getvalue()


class GuestFreeExtractionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.database_path = (
            Path.cwd() / "data" / f"guest-test-{uuid.uuid4()}.sqlite3"
        )
        database = Database(self.database_path)
        database.initialize()
        self.original_ledger = app.state.guest_extraction_ledger
        app.state.guest_extraction_ledger = GuestExtractionLedger(
            database,
            force_local=True,
        )
        self.client = TestClient(
            app,
            client=("203.0.113.7", 50000),
        )

    def tearDown(self) -> None:
        self.client.close()
        app.state.guest_extraction_ledger = self.original_ledger
        self.database_path.unlink(missing_ok=True)

    def test_free_pricing_option_opens_a_public_workspace(self) -> None:
        pricing = self.client.get("/pricing")
        self.assertEqual(pricing.status_code, 200)
        self.assertIn('window.location.href = "/free"', pricing.text)

        workspace = self.client.get("/free")
        self.assertEqual(workspace.status_code, 200)
        self.assertIn("No account required", workspace.text)

        script = self.client.get("/static/free.js")
        self.assertEqual(script.status_code, 200)
        self.assertIn('/api/free/extract', script.text)
        self.assertNotIn('/api/auth/me', script.text)

    def test_anonymous_visitor_can_extract_once_and_downloadable_text_is_returned(self) -> None:
        before = self.client.get("/api/free/status")
        self.assertEqual(before.status_code, 200)
        self.assertEqual(before.json(), {"available": True, "page_limit": 20})

        first = self.client.post(
            "/api/free/extract",
            files={"file": ("sample.pdf", make_pdf(), "application/pdf")},
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json()["page_count"], 1)
        self.assertIn("Guest extraction page 1", first.json()["raw_text"])

        after = self.client.get("/api/free/status")
        self.assertEqual(after.status_code, 200)
        self.assertFalse(after.json()["available"])

        second = self.client.post(
            "/api/free/extract",
            files={"file": ("another.pdf", make_pdf(), "application/pdf")},
        )
        self.assertEqual(second.status_code, 409)
        self.assertIn("already been used", second.json()["detail"])

    def test_over_limit_pdf_does_not_consume_the_free_extraction(self) -> None:
        response = self.client.post(
            "/api/free/extract",
            files={"file": ("too-long.pdf", make_pdf(21), "application/pdf")},
        )
        self.assertEqual(response.status_code, 413)
        self.assertIn("up to 20 pages", response.json()["detail"])

        status = self.client.get("/api/free/status")
        self.assertEqual(status.status_code, 200)
        self.assertTrue(status.json()["available"])


if __name__ == "__main__":
    unittest.main()
