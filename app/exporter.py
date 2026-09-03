from __future__ import annotations

import json
import os
import re
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from .config import Settings
from .db import Database


EXPORT_FORMATS = {"text", "json", "complete"}


def _safe_part(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip(" .")
    return cleaned or "unnamed"


def _source_parts(relative_path: str, fallback: str) -> tuple[list[str], str]:
    candidate = PurePosixPath((relative_path or fallback).replace("\\", "/"))
    parts = [_safe_part(part) for part in candidate.parts if part not in ("", ".", "..")]
    if not parts:
        parts = [_safe_part(fallback)]
    stem = _safe_part(Path(parts[-1]).stem)
    return parts[:-1], stem


def _step1_payload(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": run["id"],
        "document_id": run["document_id"],
        "original_filename": run["original_filename"],
        "source_relative_path": run["source_relative_path"],
        "source_sha256": run["sha256"],
        "parser_version": run["parser_version"],
        "status": run["status"],
        "classification_confidence": run["classification_confidence"],
        "extraction_confidence": run["extraction_confidence"],
        "ocr_used": run["ocr_used"],
        "pages": run["pages"],
        "raw_text": run["raw_text"],
        "tables": run["tables"],
        "normalized": run["normalized"],
        "provenance": run["fields"],
        "classification": run["classification"],
        "effective_category": run.get("effective_category"),
        "category_source": run.get("category_source"),
        "warnings": run["warnings"],
        "errors": run["errors"],
        "reviews": run["reviews"],
    }


class BulkExportService:
    def __init__(self, settings: Settings, database: Database):
        self.settings = settings
        self.database = database

    def create(self, export_format: str) -> tuple[Path, str, dict[str, Any]]:
        if export_format not in EXPORT_FORMATS:
            raise ValueError("Export format must be text, json, or complete")
        export_dir = self.settings.data_dir / "temporary_exports"
        export_dir.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix="feltus-export-", suffix=".zip", dir=export_dir)
        os.close(descriptor)
        output_path = Path(temporary_name)
        created_at = datetime.now(timezone.utc)
        dashboard = self.database.dashboard()
        manifest_documents: list[dict[str, Any]] = []
        written_files = 0
        try:
            with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
                for document in dashboard["documents"]:
                    manifest_item = {
                        "document_id": document["document_id"],
                        "run_id": document["run_id"],
                        "original_filename": document["original_filename"],
                        "source_relative_path": document["source_relative_path"],
                        "extraction_status": document["extraction_status"],
                        "document_type": document["classification_document_type"],
                        "category": document["effective_category"],
                        "included": False,
                        "files": [],
                    }
                    if not document["run_id"]:
                        manifest_item["reason"] = "No extraction run exists for this document."
                        manifest_documents.append(manifest_item)
                        continue
                    run = self.database.get_run(document["run_id"])
                    if not run:
                        manifest_item["reason"] = "The latest extraction run could not be read."
                        manifest_documents.append(manifest_item)
                        continue
                    folders, stem = _source_parts(document["source_relative_path"], document["original_filename"])
                    root = "/".join(folders)
                    prefix = f"{root}/" if root else ""
                    if export_format == "text":
                        entries = [(f"{prefix}{stem}.txt", run["raw_text"] or "")]
                    elif export_format == "json":
                        entries = [(f"{prefix}{stem}.json", json.dumps(_step1_payload(run), indent=2))]
                    else:
                        document_root = f"{prefix}{stem}/"
                        entries = [
                            (f"{document_root}extracted.txt", run["raw_text"] or ""),
                            (f"{document_root}step1-extraction.json", json.dumps(_step1_payload(run), indent=2)),
                        ]
                        if run["classification"]:
                            entries.append((
                                f"{document_root}classification.json",
                                json.dumps(run["classification"], indent=2),
                            ))
                        for number, interpretation in enumerate(self.database.list_interpretations(run["id"]), start=1):
                            entries.append((
                                f"{document_root}interpretation-{number}-{interpretation['interpreter_type'].lower()}.json",
                                json.dumps(interpretation, indent=2),
                            ))
                    for archive_name, content in entries:
                        archive.writestr(archive_name, content.encode("utf-8"))
                        manifest_item["files"].append(archive_name)
                        written_files += 1
                    manifest_item["included"] = True
                    manifest_documents.append(manifest_item)

                manifest = {
                    "schema_version": "1.0",
                    "export_format": export_format,
                    "created_at": created_at.isoformat(),
                    "selection": "LATEST_RUN_PER_UPLOADED_DOCUMENT",
                    "documents_total": dashboard["metrics"]["total_files"],
                    "documents_included": sum(item["included"] for item in manifest_documents),
                    "documents_not_included": sum(not item["included"] for item in manifest_documents),
                    "files_written": written_files,
                    "dashboard_metrics": dashboard["metrics"],
                    "documents": manifest_documents,
                }
                archive.writestr("manifest.json", json.dumps(manifest, indent=2).encode("utf-8"))
            download_name = f"FELTUS-all-extractions-{export_format}-{created_at.strftime('%Y%m%d-%H%M%S')}.zip"
            return output_path, download_name, manifest
        except Exception:
            output_path.unlink(missing_ok=True)
            raise
