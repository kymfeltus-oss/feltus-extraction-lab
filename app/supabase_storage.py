from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from .config import get_settings


class SupabaseStorage:
    BUCKET = "documents"

    def __init__(self) -> None:
        settings = get_settings()
        self.url = (settings.supabase_url or "").rstrip("/")
        self.anon_key = settings.supabase_anon_key or ""

    def _request(self, method: str, path: str, access_token: str, data: bytes | None = None, content_type: str = "application/octet-stream") -> dict:
        url = f"{self.url}{path}"
        headers = {
            "apikey": self.anon_key,
            "Authorization": f"Bearer {access_token}",
            "Content-Type": content_type,
            "x-upsert": "true",
        }
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8")) if resp.status != 204 else {}
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read().decode("utf-8"))
            except Exception:
                detail = None
            msg = "Supabase Storage request failed"
            if isinstance(detail, dict):
                msg = detail.get("message") or detail.get("error") or detail.get("msg") or msg
            if e.code == 403:
                msg = "Storage upload denied"
            raise RuntimeError(f"{msg} (HTTP {e.code})") from None

    def upload(self, access_token: str, storage_path: str, file_path: Path, content_type: str = "application/pdf") -> dict:
        bucket_path = f"{self.BUCKET}/{storage_path}"
        with open(file_path, "rb") as f:
            data = f.read()
        return self._request("POST", f"/storage/v1/object/{bucket_path}", access_token, data=data, content_type=content_type)
