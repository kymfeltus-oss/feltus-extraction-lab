from __future__ import annotations

import json
import hashlib
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Settings
from .db import Database


class DuplicatePlanChanged(RuntimeError):
    pass


class DuplicateCleanupService:
    def __init__(self, settings: Settings, database: Database):
        self.settings = settings
        self.database = database

    @staticmethod
    def _rank(document: dict[str, Any]) -> tuple:
        return (
            -int(document.get("latest_run_status") == "VALIDATED"),
            -int(bool(document.get("effective_category"))),
            -float(document.get("latest_extraction_confidence") or 0),
            document.get("created_at") or "",
            document["id"],
        )

    @staticmethod
    def _public(document: dict[str, Any]) -> dict[str, Any]:
        return {
            "document_id": document["id"],
            "original_filename": document["original_filename"],
            "source_relative_path": document["source_relative_path"],
            "sha256": document["sha256"],
            "uploaded_at": document["created_at"],
            "latest_run_id": document.get("latest_run_id"),
            "latest_run_status": document.get("latest_run_status"),
            "extraction_confidence": document.get("latest_extraction_confidence"),
            "effective_category": document.get("effective_category"),
        }

    def plan(self) -> dict[str, Any]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for document in self.database.duplicate_filename_documents():
            grouped[document["original_filename"].casefold()].append(document)
        groups = []
        for normalized_name, documents in grouped.items():
            ordered = sorted(documents, key=self._rank)
            survivor, removals = ordered[0], ordered[1:]
            groups.append({
                "normalized_filename": normalized_name,
                "filename": survivor["original_filename"],
                "survivor": self._public(survivor),
                "removals": [self._public(item) for item in removals],
            })
        groups.sort(key=lambda item: item["normalized_filename"])
        plan_token = hashlib.sha256(
            "|".join(
                [group["survivor"]["document_id"] for group in groups]
                + [item["document_id"] for group in groups for item in group["removals"]]
            ).encode("utf-8")
        ).hexdigest()
        return {
            "policy": "KEEP_MOST_COMPLETE_THEN_EARLIEST",
            "plan_token": plan_token,
            "duplicate_filename_groups": len(groups),
            "records_to_remove": sum(len(group["removals"]) for group in groups),
            "groups": groups,
        }

    def execute(self, expected_removals: int, plan_token: str) -> dict[str, Any]:
        plan = self.plan()
        if plan["records_to_remove"] != expected_removals or plan["plan_token"] != plan_token:
            raise DuplicatePlanChanged(
                "The duplicate set changed after review. Refresh the duplicate review before cleaning it."
            )
        if expected_removals == 0:
            return {**plan, "removed_records": 0, "backup_path": None, "quarantine_path": None}

        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        batch_root = self.settings.data_dir / "duplicate_quarantine" / stamp
        quarantine_uploads = batch_root / "uploads"
        backup_path = self.settings.data_dir / "backups" / f"extraction_lab.before_duplicate_cleanup.{stamp}.sqlite3"
        quarantine_uploads.mkdir(parents=True, exist_ok=False)
        self.database.backup_to(backup_path)
        (batch_root / "cleanup-plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")

        removal_ids = [item["document_id"] for group in plan["groups"] for item in group["removals"]]
        stored_paths = {item["id"]: Path(item["stored_path"]) for item in self.database.duplicate_filename_documents() if item["id"] in removal_ids}
        upload_root = self.settings.upload_dir.resolve()
        moved: list[tuple[Path, Path]] = []
        missing_storage: list[str] = []
        try:
            for document_id in removal_ids:
                stored_file = stored_paths[document_id]
                source_directory = stored_file.resolve().parent
                if source_directory.parent != upload_root:
                    raise RuntimeError(f"Refusing to move an unexpected upload path for document {document_id}")
                if not source_directory.exists():
                    missing_storage.append(document_id)
                    continue
                destination = quarantine_uploads / document_id
                shutil.move(str(source_directory), str(destination))
                moved.append((source_directory, destination))
            self.database.delete_documents(removal_ids)
        except Exception:
            for source, destination in reversed(moved):
                if destination.exists() and not source.exists():
                    shutil.move(str(destination), str(source))
            raise

        return {
            **plan,
            "removed_records": len(removal_ids),
            "missing_storage_records": missing_storage,
            "backup_path": str(backup_path),
            "quarantine_path": str(batch_root),
            "dashboard": self.database.dashboard()["metrics"],
        }
