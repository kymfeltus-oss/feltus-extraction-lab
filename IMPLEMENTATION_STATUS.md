# Implementation status

## Completed runtime path

`File or recursive folder selection -> sequential PDF ingestion -> evidence preservation -> every-page text/line/token/table extraction -> OCR fallback -> page-coverage validation -> immutable Step 1 persistence -> optional immutable Step 2 interpretation -> exact reconciliation -> professional review -> separate JSON exports`

There are no fabricated extraction results, sample records injected into the interface, placeholder API responses, or buttons without an implemented endpoint.

## Authority boundary

The lab owns only its isolated data directory and `lab_*` SQLite tables. It does not claim authority over or write into the existing FELTUS application. This prevents the current application's document-state and categorization problems from contaminating extraction testing.

## Verification completed

- Text-based bank statement: passed
- Text-based pay stub: passed
- Image-only scanned bank statement with actual Tesseract OCR: passed
- Generic three-page legal PDF with all page markers and text: passed
- Financial validation does not run during universal transcription: passed
- Normalized fields survive database reread: passed
- Field provenance survives database reread: passed
- Non-PDF content rejection: passed
- Immutable rerun creation: passed
- Python compilation: passed
- Browser JavaScript syntax validation: passed
- Live health, upload, processing, persistence, list-runs API flow: passed
- Step 1 export contains real page text, lines, tokens, coordinates, tables, and raw text: passed
- No financial guesses in Step 1: passed
- Multiline transaction stitching: passed
- Bank of America personal checking identification: passed
- Printed credit, debit, and balance reconciliation using exact decimals: passed
- Missing controls cannot produce a false pass: passed
- Conflicting direction signals remain unresolved: passed
- Step 2 immutable persistence and API reread: passed
- 250 MB per-PDF server limit: passed
- Folder-relative path persistence and API reread: passed
- Unsafe relative-path traversal fallback: passed
- Multi-file queue and recursive Edge/Chrome folder traversal implementation: passed syntax and contract validation

Docker image execution was not run in the build workspace because its Docker CLI is unavailable. The Compose configuration and Dockerfile are included; application-level runtime verification was performed directly with the same Python entrypoint used by the container.

## Required before connection to the main application

The main application repository and its current environment contract are required to connect this service without introducing a competing source of truth. Integration must map authenticated user and case identifiers, private object storage, Postgres/Supabase tables and row-level security, and the authoritative evidence-ingestion service. That connection is intentionally not guessed inside this isolated build.
