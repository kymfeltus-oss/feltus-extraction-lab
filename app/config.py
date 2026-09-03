from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .branding import MAX_UPLOAD_MB


@dataclass(frozen=True)
class Settings:
    root_dir: Path
    data_dir: Path
    database_path: Path
    upload_dir: Path
    max_upload_bytes: int
    ocr_dpi: int
    ocr_language: str
    supabase_url: str | None
    supabase_anon_key: str | None


def _load_env_file() -> None:
    root = Path(__file__).resolve().parents[1]
    env_path = root / ".env"
    if not env_path.exists():
        return
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            if key not in os.environ:
                os.environ[key] = value


def get_settings() -> Settings:
    _load_env_file()
    root = Path(__file__).resolve().parents[1]
    data_dir = Path(os.getenv("EXTRACTION_LAB_DATA_DIR", root / "data")).resolve()
    max_mb = int(os.getenv("EXTRACTION_LAB_MAX_UPLOAD_MB", str(MAX_UPLOAD_MB)))
    return Settings(
        root_dir=root,
        data_dir=data_dir,
        database_path=data_dir / "extraction_lab.sqlite3",
        upload_dir=data_dir / "uploads",
        max_upload_bytes=max_mb * 1024 * 1024,
        ocr_dpi=int(os.getenv("EXTRACTION_LAB_OCR_DPI", "200")),
        ocr_language=os.getenv("EXTRACTION_LAB_OCR_LANGUAGE", "eng"),
        supabase_url=os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL"),
        supabase_anon_key=os.getenv("SUPABASE_ANON_KEY") or os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY"),
    )
