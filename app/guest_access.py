from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import threading
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
        self.storage_bucket = "guest-free-extractions"
        self._storage_ready = False
        self._storage_lock = threading.Lock()
        self._reservation_times: dict[str, str] = {}
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

    @staticmethod
    def _error_message(detail: Any) -> str:
        if isinstance(detail, dict):
            return str(
                detail.get("message")
                or detail.get("error")
                or detail.get("statusCode")
                or "The free extraction ledger is unavailable."
            )
        return "The free extraction ledger is unavailable."

    def _storage_request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        allowed_errors: set[int] | None = None,
    ) -> tuple[int, dict | list | bytes]:
        headers = {
            "apikey": self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        headers.update(extra_headers or {})
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
                try:
                    content: dict | list | bytes = (
                        json.loads(raw.decode("utf-8")) if raw else {}
                    )
                except (UnicodeDecodeError, json.JSONDecodeError):
                    content = raw
                return response.status, content
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8"))
            except Exception:
                detail = None
            if exc.code in (allowed_errors or set()):
                return exc.code, detail or {}
            raise GuestLedgerUnavailable(
                f"{self._error_message(detail)} (HTTP {exc.code})"
            ) from None
        except urllib.error.URLError as exc:
            raise GuestLedgerUnavailable(
                "The free extraction ledger is unavailable."
            ) from exc

    @staticmethod
    def _encoded(value: str) -> str:
        return urllib.parse.quote(value, safe="")

    def _storage_object(self, ip_hash: str) -> str:
        return f"{self._encoded(ip_hash)}.json"

    def _ensure_storage_bucket(self) -> None:
        if self._storage_ready:
            return
        with self._storage_lock:
            if self._storage_ready:
                return
            bucket = self._encoded(self.storage_bucket)
            status, _ = self._storage_request(
                "GET",
                f"/storage/v1/bucket/{bucket}",
                allowed_errors={400, 404},
            )
            if status != 200:
                create_status, detail = self._storage_request(
                    "POST",
                    "/storage/v1/bucket",
                    {
                        "id": self.storage_bucket,
                        "name": self.storage_bucket,
                        "public": False,
                        "file_size_limit": 1024 * 1024,
                        "allowed_mime_types": ["application/json"],
                    },
                    allowed_errors={400, 409},
                )
                if create_status not in {200, 201}:
                    message = self._error_message(detail).lower()
                    if not any(
                        word in message
                        for word in ("already", "duplicate", "exists")
                    ):
                        raise GuestLedgerUnavailable(
                            "The free extraction ledger could not be prepared."
                        )
            self._storage_ready = True

    def is_available(self, ip_hash: str) -> bool:
        if self.use_remote:
            self._ensure_storage_bucket()
            status, _ = self._storage_request(
                "GET",
                "/storage/v1/object/authenticated/"
                f"{self._encoded(self.storage_bucket)}/"
                f"{self._storage_object(ip_hash)}",
                allowed_errors={400, 404},
            )
            return status in {400, 404}

        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM guest_free_extractions WHERE ip_hash = ?",
                (ip_hash,),
            ).fetchone()
        return row is None

    def reserve(self, ip_hash: str) -> None:
        if self.use_remote:
            self._ensure_storage_bucket()
            created_at = now_iso()
            status, detail = self._storage_request(
                "POST",
                "/storage/v1/object/"
                f"{self._encoded(self.storage_bucket)}/"
                f"{self._storage_object(ip_hash)}",
                {
                    "ip_hash": ip_hash,
                    "status": "processing",
                    "created_at": created_at,
                },
                extra_headers={"x-upsert": "false"},
                allowed_errors={400, 409},
            )
            if status not in {200, 201}:
                message = self._error_message(detail).lower()
                if any(
                    word in message
                    for word in ("already", "duplicate", "exists")
                ):
                    raise GuestExtractionAlreadyUsed from None
                raise GuestLedgerUnavailable(
                    "The free extraction could not be reserved."
                )
            self._reservation_times[ip_hash] = created_at
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
            self._ensure_storage_bucket()
            self._storage_request(
                "POST",
                "/storage/v1/object/"
                f"{self._encoded(self.storage_bucket)}/"
                f"{self._storage_object(ip_hash)}",
                {
                    "ip_hash": ip_hash,
                    "status": "completed",
                    "page_count": page_count,
                    "created_at": self._reservation_times.pop(
                        ip_hash,
                        now_iso(),
                    ),
                    "completed_at": now_iso(),
                },
                extra_headers={"x-upsert": "true"},
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
            self._ensure_storage_bucket()
            self._storage_request(
                "DELETE",
                f"/storage/v1/object/{self._encoded(self.storage_bucket)}",
                {"prefixes": [self._storage_object(ip_hash)]},
            )
            self._reservation_times.pop(ip_hash, None)
            return

        with self.database.connect() as conn:
            conn.execute(
                """
                DELETE FROM guest_free_extractions
                WHERE ip_hash = ? AND status = 'processing'
                """,
                (ip_hash,),
            )
