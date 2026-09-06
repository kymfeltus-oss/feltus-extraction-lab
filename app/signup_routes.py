from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from .config import get_settings


router = APIRouter(
    prefix="/api/auth",
    tags=["auth"],
)

settings = get_settings()


class RegisterRequest(BaseModel):
    full_name: str
    organization_name: str | None = None
    email: str
    password: str


class ResendRequest(BaseModel):
    email: str
    type: str = "signup"


def _build_redirect_to(request: Request) -> str:
    """
    Build the confirmation redirect URL from the current request so the
    email link points to the same host the user is browsing. This avoids
    broken provider-side SITE_URLs (e.g. a deleted Vercel preview).
    """
    forwarded_proto = request.headers.get("x-forwarded-proto")
    forwarded_host = request.headers.get("x-forwarded-host")

    scheme = forwarded_proto or request.base_url.scheme
    host = forwarded_host or request.headers.get("host") or "localhost:8000"

    redirect_to = f"{scheme}://{host}/app"
    return redirect_to


def _service_key() -> str:
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

    if not key:
        raise HTTPException(
            status_code=503,
            detail="Supabase service role key is not configured.",
        )

    return key


def _supabase_request(
    method: str,
    path: str,
    body: dict | None = None,
    *,
    service_role: bool = False,
    prefer: str | None = None,
    redirect_to: str | None = None,
):
    base_url = (settings.supabase_url or "").rstrip("/")

    if not base_url:
        raise HTTPException(
            status_code=503,
            detail="Supabase URL is not configured.",
        )

    if service_role:
        api_key = _service_key()
    else:
        api_key = settings.supabase_anon_key or ""

    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="Supabase API key is not configured.",
        )

    headers = {
        "apikey": api_key,
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    if prefer:
        headers["Prefer"] = prefer

    data = (
        json.dumps(body).encode("utf-8")
        if body is not None
        else None
    )

    url = f"{base_url}{path}"
    if redirect_to:
        # Supabase GoTrue reads redirect_to from the query string for signup.
        url += "?redirect_to=" + urllib.parse.quote(redirect_to, safe="")

    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers=headers,
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=30
        ) as response:

            raw = response.read()

            if not raw:
                return {}

            return json.loads(
                raw.decode("utf-8")
            )

    except urllib.error.HTTPError as exc:

        try:
            detail = json.loads(
                exc.read().decode("utf-8")
            )
        except Exception:
            detail = None

        message = "Supabase request failed."

        code = None
        if isinstance(detail, dict):
            code = detail.get("code") or detail.get("error_code")
            message = (
                detail.get("msg")
                or detail.get("message")
                or detail.get("error_description")
                or detail.get("error")
                or message
            )

        if code and code not in message:
            message = f"{message} ({code})"

        headers = {}
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        if retry_after:
            headers["Retry-After"] = retry_after

        raise HTTPException(
            status_code=exc.code,
            detail=message,
            headers=headers,
        ) from None


def _slugify(value: str) -> str:

    value = value.lower().strip()

    value = re.sub(
        r"[^a-z0-9]+",
        "-",
        value,
    )

    value = value.strip("-")

    if not value:
        value = "workspace"

    return (
        value[:40]
        + "-"
        + uuid.uuid4().hex[:8]
    )


def _delete_auth_user(user_id: str) -> None:

    try:
        _supabase_request(
            "DELETE",
            f"/auth/v1/admin/users/{user_id}",
            service_role=True,
        )
    except Exception:
        pass


def _delete_organization(
    organization_id: str
) -> None:

    try:
        _supabase_request(
            "DELETE",
            (
                "/rest/v1/organizations"
                f"?id=eq.{organization_id}"
            ),
            service_role=True,
            prefer="return=minimal",
        )
    except Exception:
        pass


@router.post("/register")
def register(
    payload: RegisterRequest,
    response: Response,
    request: Request,
) -> dict:

    full_name = payload.full_name.strip()
    email = payload.email.strip().lower()
    password = payload.password

    if len(full_name) < 2:
        raise HTTPException(
            status_code=400,
            detail="Please enter your name.",
        )

    if "@" not in email:
        raise HTTPException(
            status_code=400,
            detail="Please enter a valid email address.",
        )

    if len(password) < 8:
        raise HTTPException(
            status_code=400,
            detail="Password must contain at least 8 characters.",
        )

    organization_name = (
        (payload.organization_name or "").strip()
        or f"{full_name}'s Workspace"
    )

    # --------------------------------------------------------
    # CREATE SUPABASE AUTH USER
    # --------------------------------------------------------

    redirect_to = _build_redirect_to(request)

    signup = _supabase_request(
        "POST",
        "/auth/v1/signup",
        {
            "email": email,
            "password": password,
            "data": {
                "full_name": full_name,
            },
        },
        redirect_to=redirect_to,
    )

    user = signup.get("user") or {}

    user_id = user.get("id")

    if not user_id:
        detail = "Supabase did not create the user account."
        if isinstance(signup, dict):
            safe = {k: v for k, v in signup.items() if k in ("msg", "message", "error_description", "error", "code")}
            if safe:
                detail = f"{detail} Supabase response: {json.dumps(safe)}"
        raise HTTPException(status_code=400, detail=detail)

    organization_id = None

    try:

        # ----------------------------------------------------
        # CREATE ORGANIZATION
        # Billing trigger automatically creates
        # organization_billing with 10 lifetime free pages.
        # ----------------------------------------------------

        organization_rows = _supabase_request(
            "POST",
            (
                "/rest/v1/organizations"
                "?select=id,name,slug"
            ),
            {
                "name": organization_name,
                "slug": _slugify(
                    organization_name
                ),
            },
            service_role=True,
            prefer="return=representation",
        )

        if (
            not isinstance(
                organization_rows,
                list
            )
            or not organization_rows
        ):
            raise RuntimeError(
                "Organization creation failed."
            )

        organization = (
            organization_rows[0]
        )

        organization_id = (
            organization["id"]
        )

        # ----------------------------------------------------
        # MAKE NEW USER THE ORGANIZATION OWNER
        # ----------------------------------------------------

        _supabase_request(
            "POST",
            "/rest/v1/organization_members",
            {
                "organization_id":
                    organization_id,
                "user_id":
                    user_id,
                "role":
                    "owner",
            },
            service_role=True,
            prefer="return=minimal",
        )

    except Exception as exc:

        if organization_id:
            _delete_organization(
                organization_id
            )

        _delete_auth_user(
            user_id
        )

        if isinstance(
            exc,
            HTTPException
        ):
            raise

        raise HTTPException(
            status_code=500,
            detail=(
                "Account was not completed. "
                "Please try again."
            ),
        ) from exc


    # --------------------------------------------------------
    # SUPABASE MAY RETURN SESSION IMMEDIATELY
    # --------------------------------------------------------

    session = (
        signup.get("session")
        or signup
    )

    access_token = (
        session.get("access_token")
        if isinstance(session, dict)
        else None
    )

    refresh_token = (
        session.get("refresh_token")
        if isinstance(session, dict)
        else None
    )

    expires_in = (
        session.get("expires_in", 3600)
        if isinstance(session, dict)
        else 3600
    )


    # --------------------------------------------------------
    # IF SESSION EXISTS, LOG USER IN IMMEDIATELY
    # --------------------------------------------------------

    if access_token:

        response.set_cookie(
            key="access_token",
            value=access_token,
            httponly=True,
            secure=False,
            samesite="lax",
            max_age=int(expires_in),
        )

        if refresh_token:

            response.set_cookie(
                key="refresh_token",
                value=refresh_token,
                httponly=True,
                secure=False,
                samesite="lax",
                max_age=86400 * 30,
            )

        response.set_cookie(
            key="active_organization_id",
            value=organization_id,
            httponly=True,
            secure=False,
            samesite="lax",
            max_age=86400 * 30,
        )


    return {
        "created": True,
        "authenticated": bool(
            access_token
        ),
        "requires_confirmation":
            not bool(access_token),

        "user": {
            "id": user_id,
            "email": email,
            "name": full_name,
        },

        "organization": {
            "id": organization_id,
            "name": organization_name,
            "role": "owner",
        },

        "plan": {
            "code": "free_trial",
            "page_limit": 10,
            "usage_period": "lifetime",
        },
    }


@router.post("/resend")
def resend_confirmation(
    payload: ResendRequest,
    request: Request,
) -> dict:
    """
    Resend a signup confirmation email through Supabase. The request is
    rate-limited by Supabase; this endpoint passes through the provider's
    429 response without retrying or masking it.
    """
    email = payload.email.strip().lower()

    if "@" not in email:
        raise HTTPException(
            status_code=400,
            detail="Please enter a valid email address.",
        )

    body: dict = {
        "type": payload.type,
        "email": email,
    }

    redirect_to = _build_redirect_to(request)
    if redirect_to:
        body["options"] = {
            "email_redirect_to": redirect_to,
        }

    _supabase_request(
        "POST",
        "/auth/v1/resend",
        body,
    )

    return {
        "sent": True,
        "email": email,
    }
