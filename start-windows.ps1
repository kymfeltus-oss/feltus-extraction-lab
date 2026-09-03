$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

if (-not (Get-Command tesseract -ErrorAction SilentlyContinue)) {
    $CommonTesseract = "C:\Program Files\Tesseract-OCR\tesseract.exe"
    if (-not (Test-Path $CommonTesseract)) {
        Write-Warning "Tesseract OCR was not found. Text-based pages will work, but scanned pages require OCR. Run .\install-ocr-windows.ps1 if the page report says OCR is unavailable."
    }
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    py -3.12 -m venv .venv
}

& ".venv\Scripts\python.exe" -m pip install -r requirements.txt
Write-Host "Starting FELTUS Extraction Lab at http://localhost:8000" -ForegroundColor Green
& ".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
