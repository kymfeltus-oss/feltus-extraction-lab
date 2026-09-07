from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .config import get_settings
from .db import Database, now_iso


class GuestExtractionAlreadyUsed(Exception):
    pass


class GuestLedgerUnavailable(RuntimeError):
    pass


class GuestExtractionLedger:
    """Atomically records the single public extraction allowed per IP."""

    def __init__(self, database: Database, force_local: bool = False) -> None:
        settings = get_settings()
        self.database = database
        self.url = (settings.supabase_url or "").rstrip("/")
        self.service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
        self.use_remote = bool(
            not force_local and self.url and self.service_role_key
        )
        self.hash_secret = (
            os.getenv("GUEST_IP_HASH_SECRET")
            or self.service_role_key
            or "feltus-local-guest-ip-v1"
        ).encode("utf-8")
        self._initialize_local_table()

    def _initialize_local_table(self) -> None:
        with self.database.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS guest_free_extractions (
                    ip_hash TEXT PRIMARY KEY,
                    status TEXT NOT NULL
                        CHECK(status IN ('processing', 'completed')),
                    page_count INTEGER,
                    created_at TEXT NOT NULL,
                    completed_at TEXT
                )
                """
            )

    def hash_ip(self, ip_address: str) -> str:
        return hmac.new(
            self.hash_secret,
            ip_address.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _remote_request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> dict | list:
        headers = {
            "apikey": self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Prefer": "return=minimal",
        }
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            f"{self.url}{path}",
            data=data,
            method=method,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                raw = response.read()
                return json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                raise GuestExtractionAlreadyUsed from None
            try:
                detail = json.loads(exc.read().decode("utf-8"))
            except Exception:
                detail = None
            message = "The free extraction ledger is unavailable."
            if isinstance(detail, dict):
                message = detail.get("message") or detail.get("error") or message
            raise GuestLedgerUnavailable(f"{message} (HTTP {exc.code})") from None
        except urllib.error.URLError as exc:
            raise GuestLedgerUnavailable(
                "The free extraction ledger is unavailable."
            ) from exc

    @staticmethod
    def _encoded(value: str) -> str:
        return urllib.parse.quote(value, safe="")

    def is_available(self, ip_hash: str) -> bool:
        if self.use_remote:
            rows = self._remote_request(
                "GET",
                "/rest/v1/guest_free_extractions"
                f"?select=ip_hash&ip_hash=eq.{self._encoded(ip_hash)}&limit=1",
            )
            return not (isinstance(rows, list) and rows)

        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM guest_free_extractions WHERE ip_hash = ?",
                (ip_hash,),
            ).fetchone()
        return row is None

    def reserve(self, ip_hash: str) -> None:
        if self.use_remote:
            self._remote_request(
                "POST",
                "/rest/v1/guest_free_extractions",
                {
                    "ip_hash": ip_hash,
                    "status": "processing",
                    "created_at": now_iso(),
                },
            )
            return

        try:
            with self.database.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO guest_free_extractions
                        (ip_hash, status, created_at)
                    VALUES (?, 'processing', ?)
                    """,
                    (ip_hash, now_iso()),
                )
        except sqlite3.IntegrityError:
            raise GuestExtractionAlreadyUsed from None

    def complete(self, ip_hash: str, page_count: int) -> None:
        if self.use_remote:
            self._remote_request(
                "PATCH",
                "/rest/v1/guest_free_extractions"
                f"?ip_hash=eq.{self._encoded(ip_hash)}",
                {
                    "status": "completed",
                    "page_count": page_count,
                    "completed_at": now_iso(),
                },
            )
            return

        with self.database.connect() as conn:
            conn.execute(
                """
                UPDATE guest_free_extractions
                SET status = 'completed', page_count = ?, completed_at = ?
                WHERE ip_hash = ?
                """,
                (page_count, now_iso(), ip_hash),
            )

    def release(self, ip_hash: str) -> None:
        """Release a failed attempt so only a completed extraction counts."""
        if self.use_remote:
            self._remote_request(
                "DELETE",
                "/rest/v1/guest_free_extractions"
                f"?ip_hash=eq.{self._encoded(ip_hash)}&status=eq.processing",
            )
            return

        with self.database.connect() as conn:
            conn.execute(
                """
                DELETE FROM guest_free_extractions
                WHERE ip_hash = ? AND status = 'processing'
                """,
                (ip_hash,),
            )
