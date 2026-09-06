from __future__ import annotations

import hashlib
import json
import logging
import re
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from .db import Database
from .config import get_settings

import urllib.request
import urllib.error
import urllib.parse


ph = PasswordHasher(
    time_cost=3,
    memory_cost=65536,
    parallelism=4,
    hash_len=32,
    salt_len=16
)

logger = logging.getLogger("feltus.auth")


class AuthContext(BaseModel):
    user_id: str
    user_email: str
    user_name: str
    organization_id: str
    organization_name: str
    organization_slug: str
    role: str


class LoginRequest(BaseModel):
    email: str
    password: str


class SwitchOrganizationRequest(BaseModel):
    organization_id: str


class ConfirmRequest(BaseModel):
    access_token: str
    refresh_token: str | None = None
    expires_in: int | None = 3600


class AuthService:
    def __init__(self, database: Database):
        self.database = database
        self.secret_key = secrets.token_urlsafe(32)
        self.session_duration = timedelta(days=7)

    def hash_password(self, password: str) -> str:
        return ph.hash(password)

    def verify_password(self, plain_password: str, hashed_password: str) -> tuple[bool, bool]:
        """Returns (is_valid, needs_rehash) tuple."""
        # Legacy SHA-256 fallback for migration
        if re.match(r"^[a-f0-9]{64}$", hashed_password):
            is_valid = hashlib.sha256(plain_password.encode()).hexdigest() == hashed_password
            return is_valid, is_valid  # If valid, needs rehash
        
        try:
            is_valid = ph.verify(hashed_password, plain_password)
            if is_valid:
                needs_rehash = ph.check_needs_rehash(hashed_password)
                return True, needs_rehash
            return False, False
        except VerifyMismatchError:
            return False, False

    def create_user(self, email: str, name: str, password: str) -> str:
        import uuid
        user_id = str(uuid.uuid4())
        hashed_password = self.hash_password(password)
        
        with self.database.connect() as conn:
            try:
                conn.execute(
                    "INSERT INTO users (id, email, name, password_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                    (user_id, email, name, hashed_password, datetime.now(timezone.utc).isoformat())
                )
            except Exception:
                raise ValueError("User with this email already exists")
        
        return user_id

    def authenticate_user(self, email: str, password: str) -> Optional[dict]:
        with self.database.connect() as conn:
            user = conn.execute(
                "SELECT * FROM users WHERE email = ?", (email,)
            ).fetchone()
            
            if not user:
                return None
            
            user_dict = dict(user)
            is_valid, needs_rehash = self.verify_password(password, user_dict.get("password_hash", ""))
            if not is_valid:
                return None
            
            # Rehash legacy SHA-256 or outdated Argon2 hash on successful login
            if needs_rehash:
                new_hash = self.hash_password(password)
                conn.execute(
                    "UPDATE users SET password_hash = ? WHERE id = ?",
                    (new_hash, user_dict["id"])
                )
            
            return user_dict

    def get_user_organizations(self, user_id: str) -> list[dict]:
        with self.database.connect() as conn:
            rows = conn.execute("""
                SELECT o.id, o.name, o.slug, om.role
                FROM organizations o
                JOIN organization_members om ON o.id = om.organization_id
                WHERE om.user_id = ?
                ORDER BY o.name
            """, (user_id,)).fetchall()
        
        return [dict(row) for row in rows]

    def get_active_organization(self, user_id: str, organization_id: Optional[str] = None) -> Optional[dict]:
        organizations = self.get_user_organizations(user_id)
        
        if not organizations:
            return None
        
        if organization_id:
            # Verify user belongs to requested organization
            for org in organizations:
                if org["id"] == organization_id:
                    return org
            return None
        
        # Return first organization as default
        return organizations[0]

    def verify_organization_access(self, user_id: str, organization_id: str) -> bool:
        with self.database.connect() as conn:
            result = conn.execute(
                "SELECT 1 FROM organization_members WHERE user_id = ? AND organization_id = ?",
                (user_id, organization_id)
            ).fetchone()
        
        return result is not None


class SupabaseAuth:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.url = (self.settings.supabase_url or "").rstrip("/")
        self.anon_key = self.settings.supabase_anon_key or ""

    def _request(
        self, method: str, path: str, access_token: str | None = None, body: dict | None = None
    ) -> dict | list:
        url = f"{self.url}{path}"
        headers = {"apikey": self.anon_key}
        if access_token:
            headers["Authorization"] = f"Bearer {access_token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8")) if resp.status != 204 else {}
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read().decode("utf-8"))
            except Exception:
                detail = None
            msg = "Supabase request failed"
            if isinstance(detail, dict):
                msg = detail.get("msg") or detail.get("message") or detail.get("error_description") or msg
            if e.code == 401:
                msg = "Authentication required"
            raise HTTPException(status_code=e.code, detail=msg) from None

    def sign_in(self, email: str, password: str) -> dict:
        return self._request(
            "POST",
            "/auth/v1/token?grant_type=password",
            body={"email": email, "password": password},
        )

    def sign_out(self, access_token: str) -> None:
        try:
            self._request("POST", "/auth/v1/logout", access_token=access_token)
        except HTTPException as exc:
            if exc.status_code != 401:
                raise

    def get_user(self, access_token: str) -> dict:
        return self._request("GET", "/auth/v1/user", access_token=access_token)

    def get_organizations(self, user_id: str, access_token: str) -> list[dict]:
        encoded = urllib.parse.quote(user_id, safe="")
        rows = self._request(
            "GET",
            f"/rest/v1/organization_members?select=role,organization:organization_id(id,name,slug)&user_id=eq.{encoded}&limit=100",
            access_token=access_token,
        )
        orgs = []
        for row in rows:
            org = row.get("organization") or {}
            orgs.append({
                "id": org.get("id"),
                "name": org.get("name"),
                "slug": org.get("slug"),
                "role": row.get("role"),
            })
        return orgs

    def get_auth_context(self, access_token: str, active_org_id: str | None = None) -> AuthContext:
        user = self.get_user(access_token)
        user_id = user["id"]
        user_email = user.get("email", "")
        user_name = user.get("user_metadata", {}).get("name") or user_email.split("@")[0].replace(".", " ").title()
        organizations = self.get_organizations(user_id, access_token)
        if not organizations:
            raise HTTPException(status_code=403, detail="User has no organization access")
        active = None
        if active_org_id:
            for org in organizations:
                if org["id"] == active_org_id:
                    active = org
                    break
        if not active:
            active = organizations[0]
        return AuthContext(
            user_id=user_id,
            user_email=user_email,
            user_name=user_name,
            organization_id=active["id"],
            organization_name=active["name"],
            organization_slug=active["slug"],
            role=active["role"],
        )


def create_session_token() -> str:
    return secrets.token_urlsafe(32)


def _get_token(request: Request) -> str | None:
    auth_header = request.headers.get("authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return request.cookies.get("access_token")


def get_current_user(request: Request) -> AuthContext:
    supabase_auth: SupabaseAuth = getattr(request.app.state, "supabase_auth", None)
    if not supabase_auth:
        raise HTTPException(status_code=401, detail="Authentication not configured")
    access_token = _get_token(request)
    has_header = bool(request.headers.get("authorization"))
    has_cookie = bool(request.cookies.get("access_token"))
    if not access_token:
        logger.warning("Auth request rejected: no token (header=%s, cookie=%s)", has_header, has_cookie)
        raise HTTPException(status_code=401, detail="Authentication required")
    active_org_id = request.cookies.get("active_organization_id")
    try:
        return supabase_auth.get_auth_context(access_token, active_org_id)
    except HTTPException as exc:
        logger.warning("Auth token validation failed: status=%s (header=%s, cookie=%s)", exc.status_code, has_header, has_cookie)
        raise


def get_optional_user(request: Request) -> Optional[AuthContext]:
    supabase_auth: SupabaseAuth = getattr(request.app.state, "supabase_auth", None)
    if not supabase_auth:
        return None
    access_token = _get_token(request)
    if not access_token:
        return None
    try:
        active_org_id = request.cookies.get("active_organization_id")
        return supabase_auth.get_auth_context(access_token, active_org_id)
    except HTTPException:
        return None