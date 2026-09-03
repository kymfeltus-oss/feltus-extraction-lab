$ErrorActionPreference = "Stop"

if (Get-Command tesseract -ErrorAction SilentlyContinue) {
    Write-Host "Tesseract OCR is already installed." -ForegroundColor Green
    exit 0
}

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    throw "Windows Package Manager (winget) was not found. Install Tesseract OCR from https://github.com/UB-Mannheim/tesseract/wiki"
}

Write-Host "Installing the Tesseract OCR engine required for scanned PDF pages..." -ForegroundColor Cyan
winget install --exact --id UB-Mannheim.TesseractOCR --accept-package-agreements --accept-source-agreements
Write-Host "OCR installation finished. Close and restart the Extraction Lab PowerShell window." -ForegroundColor Green
