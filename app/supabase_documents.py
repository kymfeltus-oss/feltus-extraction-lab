from __future__ import annotations

import json
import urllib.error
import urllib.request

from .config import get_settings


class SupabaseDocuments:
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
        }
        if method in ("POST", "PATCH"):
            headers["Prefer"] = "return=minimal"
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
            msg = "Supabase documents request failed"
            if isinstance(detail, dict):
                msg = detail.get("message") or detail.get("error") or detail.get("msg") or msg
            raise RuntimeError(f"{msg} (HTTP {e.code})") from None

    def insert(self, access_token: str, document: dict) -> None:
        self._request("POST", "/rest/v1/lab_documents", access_token, {
            "id": document["id"],
            "organization_id": document["organization_id"],
            "original_filename": document["original_filename"],
            "source_relative_path": document.get("source_relative_path", ""),
            "stored_path": document["stored_path"],
            "sha256": document["sha256"],
            "media_type": document["media_type"],
            "size_bytes": document["size_bytes"],
            "page_count": document.get("page_count", 0),
            "created_at": document["created_at"],
        })

    def update_page_count(self, access_token: str, document_id: str, page_count: int) -> None:
        from urllib.parse import quote
        encoded = quote(document_id, safe="")
        self._request(
            "PATCH",
            f"/rest/v1/lab_documents?id=eq.{encoded}",
            access_token,
            {"page_count": page_count},
        )
