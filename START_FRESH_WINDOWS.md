# Start the clean current FELTUS Extraction Lab

This package is a fresh application build. It contains no uploaded evidence and no prior database.

## 1. Stop an older lab

If an older Extraction Lab PowerShell window is still running, click that window and press `Ctrl+C`. Only one application can use port 8000 at a time.

## 2. Extract the download

Extract `FELTUS_Universal_Evidence_Lab_Current.zip` into:

```text
C:\Users\kymfe\Downloads\FELTUS_Universal_Evidence_Lab_Current
```

Do not extract it over a previous lab folder. This build is intentionally a clean start.

## 3. Start the application

Open PowerShell and run:

```powershell
Set-Location "C:\Users\kymfe\Downloads\FELTUS_Universal_Evidence_Lab_Current"
powershell -ExecutionPolicy Bypass -File .\start-windows.ps1
```

Then open `http://localhost:8000`.

## 4. Upload the evidence

Use **Choose PDF Files**, **Choose Folder**, or drag folders into the upload area. The app accepts PDFs up to 250 MB each. Every unique folder path is accepted, including same-named or byte-identical PDFs located in different folders. Selecting the folder again skips paths already represented in the dashboard and processes only missing paths.

## 5. Export all extractions

The dashboard has three bulk-download buttons:

- **Export all text** — one `.txt` extraction per uploaded PDF.
- **Export all JSON** — one complete Step 1 `.json` contract per uploaded PDF.
- **Export complete bundle** — text, Step 1 JSON, classification, and every available parser interpretation.

All three preserve the uploaded folder paths and include `manifest.json`. Exports use the latest immutable run for every uploaded document, regardless of the active dashboard filter.

The app keeps new uploads and its database inside this folder's `data` directory. Restarting the same folder does not erase them.
