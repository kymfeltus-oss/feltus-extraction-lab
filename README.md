# FELTUS Extraction Lab

Version 3.1

This is an isolated, working universal PDF transcription and ingestion service. Step 1 preserves the original PDF, extracts every page's embedded text and tables, invokes OCR for pages with insufficient embedded text, records page coverage, stores immutable runs, and exposes source provenance for professional review. Financial and forensic logic is a separate Step 2 and does not control whether transcription succeeds.

It does not write to FELTUS production records. The service is designed to run beside the existing application until its extraction behavior is approved, after which the `/api` contract can be connected to the authoritative FELTUS ingestion path.

## What is implemented

- Individual, multi-file, and recursive folder PDF selection
- File and folder drag-and-drop with nested folder traversal
- Sequential per-file processing with visible progress and isolated failure reporting
- Preserved source-relative folder paths in the immutable document record
- Real PDF uploads with content validation and a 250 MB per-file limit
- SHA-256 evidence fingerprint and preserved original bytes
- Embedded text extraction with page coordinates using PyMuPDF
- Table extraction using pdfplumber
- Page-level Tesseract OCR fallback for scanned or text-deficient pages
- Universal page-by-page PDF transcription
- Explicit page coverage, extraction method, character count, and review status
- Immutable Step 1 page contract containing raw text, lines, tokens, PDF-normalized coordinates, tables, and extraction method
- OCR token confidence and source token identifiers
- Pure Step 1 output with no institution, account, balance, payroll, or transaction guessing
- Separate immutable Step 2 bank-statement interpretations
- Multiline transaction stitching across wrapped descriptions and page boundaries
- Section-aware debit and credit evidence with explicit unresolved states
- Exact decimal control-total and beginning-to-ending balance reconciliation
- Isolated SQLite persistence
- Immutable retry/run history
- Original-PDF versus structured-data review console
- JSON export and persisted professional decisions
- Docker deployment with a persistent data volume and health check

## Run with Docker

Docker is the recommended path because the image includes the Tesseract OCR system package.

```bash
docker compose up --build
```

Open `http://localhost:8000`.

On Windows, you may instead right-click `start-windows.ps1` and run it with PowerShell. This creates a local virtual environment, installs the service dependencies, and starts the lab. Install Tesseract separately for scanned PDFs, or use Docker, which includes it.

Uploaded evidence and the lab database are stored in the named `extraction_lab_data` volume. Back up that volume if the lab is used for material evidence. Do not expose this service publicly without adding the host application's authentication and authorization layer.

## Run directly

Python 3.12 and Tesseract must be installed.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1`.

## API contract

| Method | Route | Purpose |
| --- | --- | --- |
| `POST` | `/api/documents` | Upload and process one PDF, optionally with its folder-relative source path |
| `GET` | `/api/runs` | List immutable runs |
| `GET` | `/api/runs/{run_id}` | Read the complete Step 1 evidence contract and provenance |
| `POST` | `/api/documents/{document_id}/runs` | Reprocess the preserved original |
| `GET` | `/api/documents/{document_id}/file` | View the preserved original PDF |
| `GET` | `/api/runs/{run_id}/json` | Export page text, lines, tokens, coordinates, tables, and provenance |
| `POST` | `/api/runs/{run_id}/reviews` | Append an approval, rejection, or correction decision |
| `POST` | `/api/runs/{run_id}/interpretations/bank-statement` | Create an immutable Step 2 interpretation |
| `GET` | `/api/runs/{run_id}/interpretations` | List interpretations for a Step 1 run |
| `GET` | `/api/interpretations/{interpretation_id}/json` | Export a Step 2 interpretation |

## Production connection boundary

Before integrating with FELTUS, replace or wrap the lab's SQLite repository with the authoritative Supabase/Postgres repository, map authenticated user and case IDs into every document and run, move original evidence into the authoritative private object-storage bucket, and enforce row-level authorization. The extraction, interpretation, validation, and API response modules can remain the same.

Do not silently promote a `NEEDS_REVIEW` result into downstream analysis. Every page must produce text or receive professional disposition before Step 2 logic runs.

## Verification

From the project directory:

```bash
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

The tests generate real PDFs and verify universal transcription, OCR coordinates, database persistence, immutable provenance, multiline transaction stitching, missing-control handling, unresolved-direction handling, exact reconciliation, API persistence, and immutable reruns.

The browser upload console accepts multiple individual PDFs or an entire folder. Folder selection and drag-and-drop recurse through nested directories, skip non-PDF files with a visible count, preserve each PDF's relative path, and process every accepted PDF independently so one failure does not prevent later files from running.
