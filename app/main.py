from __future__ import annotations
from dotenv import load_dotenv
load_dotenv()

import json
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, Depends, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .auth import (
    SupabaseAuth, LoginRequest, SwitchOrganizationRequest, ConfirmRequest, get_current_user,
    get_optional_user, AuthContext, _get_token
)
from .branding import APP_NAME, APP_TITLE, APP_VERSION, MAX_UPLOAD_MB
from .config import get_settings
from .db import Database, now_iso
from .interpreter import INTERPRETER_VERSION, interpret_bank_statement
from .service import IngestionService, UploadTooLarge
from .supabase_storage import SupabaseStorage
from .supabase_usage import SupabaseUsage
from .supabase_documents import SupabaseDocuments
from .billing_routes import router as billing_router
from .signup_routes import router as signup_router



settings = get_settings()
settings.upload_dir.mkdir(parents=True, exist_ok=True)


def _is_https(request: Request) -> bool:
    """Use the forwarded protocol (Vercel, proxy) or the request scheme."""
    forwarded_proto = request.headers.get("x-forwarded-proto")
    return (forwarded_proto or request.base_url.scheme) == "https"
database = Database(settings.database_path)
database.initialize()
service = IngestionService(settings, database)
supabase_auth = SupabaseAuth()
supabase_storage = SupabaseStorage()
supabase_usage = SupabaseUsage()
supabase_documents = SupabaseDocuments()

app = FastAPI(title=APP_TITLE, version=APP_VERSION)
app.include_router(billing_router)
app.include_router(signup_router)
app.mount("/static", StaticFiles(directory=settings.root_dir / "static"), name="static")
app.mount("/images", StaticFiles(directory=settings.root_dir / "public" / "images"), name="images")

# Store database and auth service in app state for dependency injection
app.state.database = database
app.state.supabase_auth = supabase_auth
app.state.supabase_storage = supabase_storage
app.state.supabase_usage = supabase_usage
app.state.supabase_documents = supabase_documents
app.state.settings = settings


class ReviewRequest(BaseModel):
    decision: str
    note: str = ""


def _auth_response(auth_context: AuthContext, organizations: list[dict] | None = None) -> dict:
    orgs = organizations or []
    return {
        "user": {
            "id": auth_context.user_id,
            "email": auth_context.user_email,
            "name": auth_context.user_name
        },
        "organizations": [
            {
                "id": org["id"],
                "name": org["name"],
                "slug": org["slug"],
                "role": org["role"]
            }
            for org in orgs
        ],
        "active_organization": {
            "id": auth_context.organization_id,
            "name": auth_context.organization_name,
            "slug": auth_context.organization_slug,
            "role": auth_context.role
        }
    }


@app.post("/api/auth/login")
def login(request: Request, response: Response, login: LoginRequest) -> dict:
    supabase_auth = request.app.state.supabase_auth
    session = supabase_auth.sign_in(login.email, login.password)
    access_token = session["access_token"]
    refresh_token = session.get("refresh_token", "")
    expires_in = session.get("expires_in", 3600)

    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=_is_https(request),
        samesite="lax",
        max_age=expires_in
    )
    if refresh_token:
        response.set_cookie(
            key="refresh_token",
            value=refresh_token,
            httponly=True,
            secure=_is_https(request),
            samesite="lax",
            max_age=expires_in * 24 * 7
        )

    auth_context = supabase_auth.get_auth_context(access_token)
    organizations = supabase_auth.get_organizations(auth_context.user_id, access_token)
    return _auth_response(auth_context, organizations)


@app.post("/api/auth/logout")
def logout(request: Request, response: Response, auth_context: AuthContext = Depends(get_current_user)) -> dict:
    access_token = _get_token(request)
    if access_token:
        request.app.state.supabase_auth.sign_out(access_token)

    secure = _is_https(request)
    response.delete_cookie("access_token", secure=secure)
    response.delete_cookie("refresh_token", secure=secure)
    response.delete_cookie("active_organization_id", secure=secure)
    return {"message": "Logged out successfully"}


@app.post("/api/auth/confirm")
def confirm(
    request: Request,
    response: Response,
    confirm: ConfirmRequest,
) -> dict:
    """
    Exchange a Supabase access token (from an email-confirmation or
    magic-link redirect) for a local session cookie. The token is validated
    by calling Supabase /auth/v1/user, so the backend continues to enforce
    Supabase JWT validation without storing any secret material.
    """
    supabase_auth = request.app.state.supabase_auth
    auth_context = supabase_auth.get_auth_context(confirm.access_token)
    access_token = confirm.access_token
    refresh_token = confirm.refresh_token or ""
    expires_in = confirm.expires_in or 3600

    response.set_cookie(
        key="access_token",
        value=access_token,
        httponly=True,
        secure=_is_https(request),
        samesite="lax",
        max_age=expires_in
    )
    if refresh_token:
        response.set_cookie(
            key="refresh_token",
            value=refresh_token,
            httponly=True,
            secure=_is_https(request),
            samesite="lax",
            max_age=expires_in * 24 * 7
        )
    response.set_cookie(
        key="active_organization_id",
        value=auth_context.organization_id,
        httponly=True,
        secure=_is_https(request),
        samesite="lax",
        max_age=86400 * 30
    )

    organizations = supabase_auth.get_organizations(auth_context.user_id, access_token)
    return _auth_response(auth_context, organizations)


@app.get("/api/auth/me")
def get_current_auth(request: Request, auth_context: AuthContext = Depends(get_current_user)) -> dict:
    supabase_auth = request.app.state.supabase_auth
    organizations = supabase_auth.get_organizations(auth_context.user_id, _get_token(request))
    return _auth_response(auth_context, organizations)


@app.post("/api/auth/switch-organization")
def switch_organization(
    req: Request,
    request: SwitchOrganizationRequest,
    response: Response,
    auth_context: AuthContext = Depends(get_current_user),
) -> dict:
    supabase_auth = req.app.state.supabase_auth
    access_token = _get_token(req)
    organizations = supabase_auth.get_organizations(auth_context.user_id, access_token)
    target_org = None
    for org in organizations:
        if org["id"] == request.organization_id:
            target_org = org
            break

    if not target_org:
        raise HTTPException(status_code=404, detail="Organization not found")

    response.set_cookie(
        key="active_organization_id",
        value=target_org["id"],
        httponly=True,
        secure=_is_https(request),
        samesite="lax",
        max_age=86400 * 30
    )

    return {
        "active_organization": {
            "id": target_org["id"],
            "name": target_org["name"],
            "slug": target_org["slug"],
            "role": target_org["role"]
        }
    }


@app.get("/app", response_class=HTMLResponse)
def index() -> FileResponse:
    return FileResponse(settings.root_dir / "static" / "index.html")

@app.get("/", response_class=HTMLResponse)
@app.get("/pricing", response_class=HTMLResponse)
def pricing() -> FileResponse:
    return FileResponse(
        settings.root_dir / "static" / "pricing.html"
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "database": str(settings.database_path.name), "max_upload_mb": MAX_UPLOAD_MB}


@app.post("/api/documents", status_code=201)
async def upload_document(
    http_request: Request,
    file: UploadFile = File(...),
    relative_path: str | None = Form(default=None),
    auth_context: AuthContext = Depends(get_current_user)
) -> dict:
    access_token = _get_token(http_request)
    if not access_token:
        raise HTTPException(status_code=401, detail="Supabase session required for upload")
    try:
        document = service.store_upload(
            file.file, file.filename or "document.pdf", file.content_type, relative_path,
            organization_id=auth_context.organization_id
        )
        storage_path = f"{auth_context.organization_id}/{document['id']}/original.pdf"
        http_request.app.state.supabase_storage.upload(access_token, storage_path, Path(document["stored_path"]))
        database.update_supabase_storage_path(document["id"], auth_context.organization_id, storage_path)
        http_request.app.state.supabase_documents.insert(access_token, {
            "id": document["id"],
            "organization_id": auth_context.organization_id,
            "original_filename": document["original_filename"],
            "source_relative_path": document["source_relative_path"],
            "stored_path": storage_path,
            "sha256": document["sha256"],
            "media_type": document["media_type"],
            "size_bytes": document["size_bytes"],
            "page_count": document["page_count"],
            "created_at": document["created_at"],
        })
        run_id = service.process(document)
        result = database.get_run(run_id, organization_id=auth_context.organization_id)
        if result and result.get("status") in ("VALIDATED", "NEEDS_REVIEW"):
            updated_doc = database.get_document(document["id"], organization_id=auth_context.organization_id)
            page_count = updated_doc["page_count"] if updated_doc else 0
            http_request.app.state.supabase_documents.update_page_count(access_token, document["id"], page_count)
            http_request.app.state.supabase_usage.create_usage_record(
                access_token,
                auth_context.organization_id,
                auth_context.user_id,
                document["id"],
                page_count,
            )
        return {"document_id": document["id"], "run_id": run_id, "status": result["status"] if result else "FAILED",
                "filename": document["original_filename"], "relative_path": document["source_relative_path"]}
    except UploadTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/runs")
def list_runs(auth_context: AuthContext = Depends(get_current_user)) -> list[dict]:
    return database.list_runs(organization_id=auth_context.organization_id)


@app.get("/api/runs/{run_id}")
def get_run(run_id: str, auth_context: AuthContext = Depends(get_current_user)) -> dict:
    run = database.get_run(run_id, organization_id=auth_context.organization_id)
    if not run:
        raise HTTPException(status_code=404, detail="Extraction run not found")
    return run


@app.post("/api/documents/{document_id}/runs", status_code=201)
def rerun_document(document_id: str, auth_context: AuthContext = Depends(get_current_user)) -> dict:
    document = database.get_document(document_id, organization_id=auth_context.organization_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    run_id = service.process(document)
    run = database.get_run(run_id, organization_id=auth_context.organization_id)
    return {"document_id": document_id, "run_id": run_id, "status": run["status"] if run else "FAILED"}


@app.get("/api/documents/{document_id}/file")
def original_file(document_id: str, auth_context: AuthContext = Depends(get_current_user)) -> FileResponse:
    document = database.get_document(document_id, organization_id=auth_context.organization_id)
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    return FileResponse(document["stored_path"], media_type="application/pdf", filename=document["original_filename"], content_disposition_type="inline")


@app.get("/api/runs/{run_id}/json")
def export_json(run_id: str, auth_context: AuthContext = Depends(get_current_user)) -> Response:
    run = database.get_run(run_id, organization_id=auth_context.organization_id)
    if not run:
        raise HTTPException(status_code=404, detail="Extraction run not found")
    payload = {
        "run_id": run["id"], "document_id": run["document_id"],
        "original_filename": run["original_filename"], "source_relative_path": run["source_relative_path"],
        "source_sha256": run["sha256"],
        "parser_version": run["parser_version"], "status": run["status"],
        "classification_confidence": run["classification_confidence"],
        "extraction_confidence": run["extraction_confidence"], "ocr_used": run["ocr_used"],
        "pages": run["pages"], "raw_text": run["raw_text"], "tables": run["tables"],
        "normalized": run["normalized"], "provenance": run["fields"],
        "warnings": run["warnings"], "errors": run["errors"],
    }
    return Response(json.dumps(payload, indent=2), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="extraction-{run_id}.json"'})


@app.post("/api/runs/{run_id}/interpretations/bank-statement", status_code=201)
def create_bank_statement_interpretation(run_id: str, auth_context: AuthContext = Depends(get_current_user)) -> dict:
    run = database.get_run(run_id, organization_id=auth_context.organization_id)
    if not run:
        raise HTTPException(status_code=404, detail="Extraction run not found")
    if run["status"] == "FAILED":
        raise HTTPException(status_code=409, detail="A failed extraction cannot be interpreted")
    try:
        result = interpret_bank_statement(run)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    interpretation_id = str(uuid.uuid4())
    database.insert_interpretation({
        "id": interpretation_id, "source_run_id": run_id, "interpreter_type": "BANK_STATEMENT",
        "interpreter_version": INTERPRETER_VERSION, "status": result["reconciliation"]["status"],
        "result": result, "created_at": now_iso(),
    })
    return database.get_interpretation(interpretation_id)


@app.get("/api/runs/{run_id}/interpretations")
def list_run_interpretations(run_id: str, auth_context: AuthContext = Depends(get_current_user)) -> list[dict]:
    if not database.get_run(run_id, organization_id=auth_context.organization_id):
        raise HTTPException(status_code=404, detail="Extraction run not found")
    return database.list_interpretations(run_id, organization_id=auth_context.organization_id)


@app.get("/api/interpretations/{interpretation_id}/json")
def export_interpretation(interpretation_id: str, auth_context: AuthContext = Depends(get_current_user)) -> Response:
    interpretation = database.get_interpretation(interpretation_id, organization_id=auth_context.organization_id)
    if not interpretation:
        raise HTTPException(status_code=404, detail="Interpretation not found")
    return Response(json.dumps(interpretation, indent=2), media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="interpretation-{interpretation_id}.json"'})


@app.post("/api/runs/{run_id}/reviews", status_code=201)
def review_run(run_id: str, review: ReviewRequest, auth_context: AuthContext = Depends(get_current_user)) -> dict:
    decision = review.decision.upper()
    if decision not in {"APPROVED", "REJECTED", "NEEDS_CORRECTION"}:
        raise HTTPException(status_code=400, detail="Invalid review decision")
    try:
        run = database.get_run(run_id, organization_id=auth_context.organization_id)
        if not run:
            raise HTTPException(status_code=404, detail="Extraction run not found")
        database.add_review(run_id, decision, review.note.strip(), organization_id=auth_context.organization_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Extraction run not found") from exc
    return {"run_id": run_id, "decision": decision}


@app.get("/api/dashboard")
def dashboard(auth_context: AuthContext = Depends(get_current_user)) -> dict:
    return database.dashboard(organization_id=auth_context.organization_id)


@app.get("/api/usage")
def usage(auth_context: AuthContext = Depends(get_current_user)) -> dict:
    return database.get_usage(organization_id=auth_context.organization_id)


@app.get("/api/branding")
def get_branding(auth_context: AuthContext = Depends(get_current_user)) -> dict:
    return database.get_branding(organization_id=auth_context.organization_id)


@app.get("/api/branding/public")
def get_public_branding(request: Request, slug: str | None = None) -> dict:
    hostname = request.headers.get("host")
    return database.get_public_branding(slug=slug, hostname=hostname)




