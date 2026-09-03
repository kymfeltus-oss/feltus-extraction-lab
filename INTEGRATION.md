# Main application integration

This directory contains the isolated FastAPI Extraction Lab engine and its original browser interface. The main Next.js application exposes it at `/extraction-lab` and proxies `/extraction-lab/service/*` to the loopback service.

The integrated runtime does not copy or migrate evidence. By default, `scripts/extraction-lab/Start-ExtractionLab.ps1` points `EXTRACTION_LAB_DATA_DIR` to:

`C:\Users\kymfe\Downloads\FELTUS_Universal_Evidence_Lab_Current\data`

That directory remains the authority for `extraction_lab.sqlite3`, uploaded PDFs, categories, extractions, and immutable run history. Override `EXTRACTION_LAB_DATA_DIR` only when deliberately selecting another complete Lab data directory.

## First setup

```powershell
.\scripts\extraction-lab\Setup-ExtractionLab.ps1 -IncludeDevDependencies
```

## Start the complete local application

```powershell
.\scripts\extraction-lab\Start-FeltusWithExtractionLab.ps1
```

The main app starts at `http://127.0.0.1:3000`; the FastAPI engine listens only on `127.0.0.1:8000`.

To use another backend port, pass the same port to the combined script. To run the services separately, set `EXTRACTION_LAB_SERVICE_URL` before starting Next.js and pass the matching `-Port` to `Start-ExtractionLab.ps1`.

The nested `.gitignore` excludes local service data, virtual environments, runtime logs, and Python caches. The existing external data directory is outside this repository and is not part of Next.js output tracing or production builds.
