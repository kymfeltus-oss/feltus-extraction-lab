from __future__ import annotations

import argparse
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .config import get_settings
from .db import Database


def archive_and_clear_active_documents(data_dir: Path) -> dict[str, str | int | None]:
    """Archive active uploads and clear their database records.

    This operation is intentionally recoverable: it takes a SQLite backup first and
    moves, rather than deletes, the upload directory into the external Lab data
    directory. The service must not be running while this operation executes.
    """
    data_dir = data_dir.resolve()
    database_path = data_dir / "extraction_lab.sqlite3"
    upload_dir = data_dir / "uploads"
    if not database_path.is_file():
        raise FileNotFoundError(f"Extraction Lab database was not found at {database_path}")
    if upload_dir.resolve().parent != data_dir:
        raise RuntimeError("Refusing to archive an upload directory outside the configured data directory")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_root = data_dir / "archived_clears" / stamp
    backup_path = data_dir / "backups" / f"extraction_lab.before_clear.{stamp}.sqlite3"
    archived_uploads = archive_root / "uploads"
    database = Database(database_path)
    database.backup_to(backup_path)

    upload_was_archived = False
    try:
        if upload_dir.exists():
            archive_root.mkdir(parents=True, exist_ok=False)
            shutil.move(str(upload_dir), str(archived_uploads))
            upload_was_archived = True
        upload_dir.mkdir(parents=True, exist_ok=True)
        removed_documents = database.delete_all_documents()
    except Exception:
        if upload_was_archived and archived_uploads.exists():
            if upload_dir.exists() and not any(upload_dir.iterdir()):
                upload_dir.rmdir()
            if not upload_dir.exists():
                shutil.move(str(archived_uploads), str(upload_dir))
        raise

    return {
        "removed_documents": removed_documents,
        "backup_path": str(backup_path),
        "archived_uploads_path": str(archived_uploads) if upload_was_archived else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Archive Extraction Lab uploads and clear active records.")
    parser.add_argument("--data-dir", type=Path, default=get_settings().data_dir)
    args = parser.parse_args()
    result = archive_and_clear_active_documents(args.data_dir)
    print(f"Removed {result['removed_documents']} active documents.")
    print(f"Database backup: {result['backup_path']}")
    if result["archived_uploads_path"]:
        print(f"Archived uploads: {result['archived_uploads_path']}")


if __name__ == "__main__":
    main()
