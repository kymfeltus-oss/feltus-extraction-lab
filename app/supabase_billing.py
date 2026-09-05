from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

from .config import get_settings


class SupabaseBilling:
    def __init__(self) -> None:
        settings = get_settings()

        self.url = (settings.supabase_url or "").rstrip("/")
        self.service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

    @property
    def configured(self) -> bool:
        return bool(self.url and self.service_role_key)

    def _request(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        prefer: str | None = None,
    ) -> dict | list:

        if not self.configured:
            raise RuntimeError(
                "Supabase billing is not configured. "
                "SUPABASE_SERVICE_ROLE_KEY is required."
            )

        headers = {
            "apikey": self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        if prefer:
            headers["Prefer"] = prefer

        data = json.dumps(body).encode("utf-8") if body is not None else None

        req = urllib.request.Request(
            f"{self.url}{path}",
            data=data,
            method=method,
            headers=headers,
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()

                if not raw:
                    return {}

                return json.loads(raw.decode("utf-8"))

        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8"))
            except Exception:
                detail = None

            message = "Supabase billing request failed"

            if isinstance(detail, dict):
                message = (
                    detail.get("message")
                    or detail.get("error")
                    or detail.get("msg")
                    or message
                )

            raise RuntimeError(
                f"{message} (HTTP {exc.code})"
            ) from None

    @staticmethod
    def _encode(value: str) -> str:
        return urllib.parse.quote(str(value), safe="")

    def get_billing(self, organization_id: str) -> dict | None:
        org = self._encode(organization_id)

        rows = self._request(
            "GET",
            (
                "/rest/v1/organization_billing"
                "?select=*"
                f"&organization_id=eq.{org}"
                "&limit=1"
            ),
        )

        if isinstance(rows, list) and rows:
            return rows[0]

        return None

    def get_plan(self, plan_code: str) -> dict | None:
        code = self._encode(plan_code)

        rows = self._request(
            "GET",
            (
                "/rest/v1/billing_plans"
                "?select=*"
                f"&code=eq.{code}"
                "&limit=1"
            ),
        )

        if isinstance(rows, list) and rows:
            return rows[0]

        return None

    def update_billing(
        self,
        organization_id: str,
        values: dict,
    ) -> None:

        org = self._encode(organization_id)

        self._request(
            "PATCH",
            (
                "/rest/v1/organization_billing"
                f"?organization_id=eq.{org}"
            ),
            values,
            prefer="return=minimal",
        )

    def get_usage_rows(
        self,
        organization_id: str,
        start_iso: str | None = None,
        end_iso: str | None = None,
    ) -> list[dict]:

        org = self._encode(organization_id)

        path = (
            "/rest/v1/usage_records"
            "?select=page_count,created_at"
            f"&organization_id=eq.{org}"
            "&event_type=eq.PDF_EXTRACTION"
        )

        if start_iso:
            path += (
                "&created_at=gte."
                + self._encode(start_iso)
            )

        if end_iso:
            path += (
                "&created_at=lt."
                + self._encode(end_iso)
            )

        rows = self._request("GET", path)

        return rows if isinstance(rows, list) else []

    def get_usage_status(
        self,
        organization_id: str,
    ) -> dict:

        billing = self.get_billing(organization_id)

        if not billing:
            raise RuntimeError(
                "Organization billing record was not found."
            )

        plan_code = billing.get("plan_code") or "free_trial"
        plan = self.get_plan(plan_code)

        if not plan:
            raise RuntimeError(
                f"Billing plan '{plan_code}' was not found."
            )

        if plan_code == "free_trial":
            limit = int(
                billing.get("free_trial_page_limit") or 10
            )
            start_iso = None
            end_iso = None
        else:
            limit = plan.get("page_limit")
            start_iso = billing.get("current_period_start")
            end_iso = billing.get("current_period_end")

        rows = self.get_usage_rows(
            organization_id,
            start_iso,
            end_iso,
        )

        pages_used = sum(
            max(int(row.get("page_count") or 0), 0)
            for row in rows
        )

        if limit is None:
            page_limit = None
            pages_remaining = None
        else:
            page_limit = int(limit)
            pages_remaining = max(
                page_limit - pages_used,
                0,
            )

        return {
            "organization_id": organization_id,
            "plan_code": plan_code,
            "plan_name": plan.get("display_name"),
            "status": billing.get("status"),
            "usage_period": plan.get("usage_period"),
            "page_limit": page_limit,
            "pages_used": pages_used,
            "pages_remaining": pages_remaining,
            "current_period_start": billing.get("current_period_start"),
            "current_period_end": billing.get("current_period_end"),
            "cancel_at_period_end": bool(
                billing.get("cancel_at_period_end", False)
            ),
            "stripe_customer_id": billing.get("stripe_customer_id"),
        }