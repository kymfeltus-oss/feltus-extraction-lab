from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid

from .config import get_settings
from .db import now_iso


class SupabaseUsage:
    def __init__(self) -> None:
        settings = get_settings()
        self.url = (settings.supabase_url or "").rstrip("/")
        self.anon_key = settings.supabase_anon_key or ""

    def _request(
        self, method: str, path: str, access_token: str, body: dict | None = None
    ) -> dict | list:
        url = f"{self.url}{path}"
        headers = {
            "apikey": self.anon_key,
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Prefer": "return=minimal",
        }
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8")) if resp.status not in (204, 201) else {}
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read().decode("utf-8"))
            except Exception:
                detail = None
            msg = "Supabase usage write failed"
            if isinstance(detail, dict):
                msg = detail.get("message") or detail.get("error") or detail.get("msg") or msg
            raise RuntimeError(f"{msg} (HTTP {e.code})") from None

    def create_usage_record(
        self,
        access_token: str,
        organization_id: str,
        user_id: str,
        document_id: str,
        page_count: int,
    ) -> None:
        self._request(
            "POST",
            "/rest/v1/usage_records",
            access_token,
            {
                "id": str(uuid.uuid4()),
                "organization_id": organization_id,
                "user_id": user_id,
                "event_type": "PDF_EXTRACTION",
                "document_id": document_id,
                "page_count": page_count,
                "created_at": now_iso(),
            },
        )
