from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from .config import Settings
from .db import Database, now_iso
from .extractor import PARSER_VERSION, extract_pdf
from .validation import validate_extraction


class UploadTooLarge(ValueError):
    pass


class IngestionService:
    def __init__(self, settings: Settings, database: Database):
        self.settings = settings
        self.database = database
        self.default_organization_id = self._get_default_organization_id()

    def _get_default_organization_id(self) -> str | None:
        org = self.database.get_default_organization()
        if org:
            return org["id"]
        # If default organization doesn't exist, return None (will be handled during initialization)
        return None

    def store_upload(self, stream: BinaryIO, filename: str, content_type: str | None, source_relative_path: str | None = None, organization_id: str | None = None) -> dict:
        safe_name = Path(filename or "document.pdf").name
        if not safe_name.lower().endswith(".pdf") or content_type not in (None, "", "application/pdf", "application/octet-stream"):
            raise ValueError("Only PDF files are accepted")
        source_path = self._safe_relative_path(source_relative_path or safe_name, safe_name)
        document_id = str(uuid.uuid4())
        target_dir = self.settings.upload_dir / document_id
        target_dir.mkdir(parents=True, exist_ok=False)
        target = target_dir / "original.pdf"
        digest = hashlib.sha256()
        size = 0
        try:
            with target.open("wb") as output:
                while chunk := stream.read(1024 * 1024):
                    size += len(chunk)
                    if size > self.settings.max_upload_bytes:
                        raise UploadTooLarge(f"PDF exceeds the {self.settings.max_upload_bytes // 1024 // 1024} MB limit")
                    digest.update(chunk)
                    output.write(chunk)
            if size < 5 or target.read_bytes()[:5] != b"%PDF-":
                raise ValueError("File content is not a PDF")
            row = {
                "id": document_id,
                "original_filename": safe_name,
                "source_relative_path": source_path,
                "stored_path": str(target),
                "supabase_storage_path": "",
                "sha256": digest.hexdigest(),
                "media_type": "application/pdf",
                "size_bytes": size,
                "page_count": 0,
                "organization_id": organization_id or self.default_organization_id,
                "created_at": now_iso(),
            }
            self.database.insert_document(row)
            return row
        except Exception:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise

    @staticmethod
    def _safe_relative_path(value: str, fallback_name: str) -> str:
        candidate = PurePosixPath(value.replace("\\", "/"))
        if candidate.is_absolute() or ".." in candidate.parts:
            return fallback_name
        clean_parts = [part for part in candidate.parts if part not in ("", ".")]
        return "/".join(clean_parts) if clean_parts else fallback_name

    def process(self, document: dict) -> str:
        run_id = str(uuid.uuid4())
        self.database.insert_run({
            "id": run_id,
            "document_id": document["id"],
            "parser_version": PARSER_VERSION,
            "organization_id": document.get("organization_id") or self.default_organization_id,
            "started_at": now_iso(),
        })
        try:
            extraction = extract_pdf(
                Path(document["stored_path"]),
                self.settings.ocr_dpi,
                self.settings.ocr_language,
            )
            self.database.update_page_count(document["id"], extraction.page_count)
            validation_warnings, errors, extraction_confidence = validate_extraction(extraction)
            pages_with_text = sum(bool(page.raw_text.strip()) for page in extraction.pages)
            page_contracts = [page.to_contract() for page in extraction.pages]
            normalized = {"schema_version": "2.0", "document_type": "PDF_DOCUMENT", "source_extraction": {
                "total_pages": extraction.page_count, "pages_with_text": pages_with_text,
                "pages_needing_review": extraction.page_count - pages_with_text,
                "tables_detected": len(extraction.tables),
                "page_results": [{"page_number": page.page_number, "method": page.method,
                    "character_count": page.character_count, "line_count": len(page.lines),
                    "text_quality": page.text_quality, "needs_review": page.needs_review}
                    for page in extraction.pages],
            }}
            fields = [{"field_path": f"pages.{page.page_number}.transcription",
                "value_json": json.dumps({"method": page.method, "character_count": page.character_count, "line_count": len(page.lines)}),
                "page_number": page.page_number, "source_text": page.raw_text[:500],
                "source_bbox_json": None, "confidence": 1.0 if page.raw_text.strip() else 0.0}
                for page in extraction.pages]
            warnings = extraction.warnings + validation_warnings
            status = "VALIDATED" if not errors and extraction_confidence == 1.0 else "NEEDS_REVIEW"
            self.database.complete_run(run_id, {
                "status": status,
                "document_type": "PDF_DOCUMENT",
                "classification_confidence": 0.0,
                "extraction_confidence": extraction_confidence,
                "ocr_used": extraction.ocr_used,
                "raw_text": extraction.raw_text,
                "pages": page_contracts,
                "tables": extraction.tables,
                "normalized": normalized,
                "warnings": warnings,
                "errors": errors,
                "organization_id": document.get("organization_id") or self.default_organization_id,
            }, fields)
        except Exception as exc:
            self.database.fail_run(run_id, str(exc))
        return run_id
