from __future__ import annotations

import ipaddress
import logging
import os
import uuid
from pathlib import Path

import fitz
from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile

from .extractor import extract_pdf
from .guest_access import (
    GuestExtractionAlreadyUsed,
    GuestExtractionLedger,
    GuestLedgerUnavailable,
)
from .service import UploadTooLarge


router = APIRouter()
logger = logging.getLogger("feltus.guest")

FREE_GUEST_PAGE_LIMIT = 20


def _client_ip(request: Request) -> str:
    candidate = ""
    trust_forwarded = os.getenv("TRUST_PROXY_IP_HEADERS", "").lower() in {
        "1",
        "true",
        "yes",
    }

    if os.getenv("VERCEL"):
        candidate = (
            request.headers.get("x-vercel-forwarded-for")
            or request.headers.get("x-forwarded-for")
            or ""
        )
    elif trust_forwarded:
        candidate = (
            request.headers.get("cf-connecting-ip")
            or request.headers.get("x-forwarded-for")
            or ""
        )

    candidate = candidate.split(",", 1)[0].strip()
    if not candidate and request.client:
        candidate = request.client.host
    if not candidate:
        raise HTTPException(
            status_code=400,
            detail="Your network address could not be verified.",
        )

    try:
        return ipaddress.ip_address(candidate).compressed
    except ValueError:
        # ASGI test clients and local development may use a host label.
        return candidate.casefold()


def _guest_identity(request: Request) -> tuple[GuestExtractionLedger, str]:
    ledger: GuestExtractionLedger = request.app.state.guest_extraction_ledger
    return ledger, ledger.hash_ip(_client_ip(request))


def _write_upload(upload: UploadFile, target: Path, max_bytes: int) -> None:
    size = 0
    with target.open("wb") as output:
        while chunk := upload.file.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                raise UploadTooLarge(
                    f"PDF exceeds the {max_bytes // 1024 // 1024} MB limit"
                )
            output.write(chunk)

    if size < 5 or target.read_bytes()[:5] != b"%PDF-":
        raise ValueError("File content is not a PDF")


def _page_count(path: Path) -> int:
    try:
        document = fitz.open(path)
    except Exception as exc:
        raise ValueError("The uploaded file is not a readable PDF") from exc
    try:
        if document.needs_pass:
            raise ValueError(
                "Password-protected PDFs must be unlocked before upload"
            )
        return document.page_count
    finally:
        document.close()


def _already_used() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail=(
            "The free extraction has already been used from "
            "this IP address."
        ),
    )


@router.get("/api/free/status")
def guest_status(request: Request, response: Response) -> dict:
    response.headers["Cache-Control"] = "private, no-store"
    ledger, ip_hash = _guest_identity(request)
    try:
        available = ledger.is_available(ip_hash)
    except GuestLedgerUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "available": available,
        "page_limit": FREE_GUEST_PAGE_LIMIT,
    }


@router.post("/api/free/extract")
async def guest_extract(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
) -> dict:
    response.headers["Cache-Control"] = "private, no-store"
    filename = Path(file.filename or "document.pdf").name
    if (
        not filename.lower().endswith(".pdf")
        or file.content_type
        not in (None, "", "application/pdf", "application/octet-stream")
    ):
        raise HTTPException(status_code=400, detail="Only PDF files are accepted")

    settings = request.app.state.settings
    ledger, ip_hash = _guest_identity(request)
    reserved = False
    completed = False
    path = settings.upload_dir / f"guest-{uuid.uuid4()}.pdf"

    try:
        try:
            if not ledger.is_available(ip_hash):
                raise _already_used()
        except GuestLedgerUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        _write_upload(file, path, settings.max_upload_bytes)
        page_count = _page_count(path)
        if page_count < 1:
            raise HTTPException(
                status_code=400,
                detail="The PDF does not contain any pages.",
            )
        if page_count > FREE_GUEST_PAGE_LIMIT:
            raise HTTPException(
                status_code=413,
                detail=(
                    "The free extraction accepts one PDF with up to "
                    f"{FREE_GUEST_PAGE_LIMIT} pages."
                ),
            )

        try:
            ledger.reserve(ip_hash)
            reserved = True
        except GuestExtractionAlreadyUsed:
            raise _already_used() from None
        except GuestLedgerUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        extraction = extract_pdf(
            path,
            settings.ocr_dpi,
            settings.ocr_language,
        )
        try:
            ledger.complete(ip_hash, extraction.page_count)
        except GuestLedgerUnavailable:
            # The unique reservation already prevents another extraction.
            logger.exception("Could not mark guest extraction complete")
        completed = True
        return {
            "original_filename": filename,
            "page_count": extraction.page_count,
            "ocr_used": extraction.ocr_used,
            "raw_text": extraction.raw_text,
            "warnings": extraction.warnings,
        }
    except UploadTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Guest PDF extraction failed")
        raise HTTPException(
            status_code=500,
            detail="The PDF could not be extracted. Please try again.",
        ) from exc
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.exception("Could not delete temporary guest PDF")
        if reserved and not completed:
            try:
                ledger.release(ip_hash)
            except GuestLedgerUnavailable:
                logger.exception("Could not release failed guest extraction")
