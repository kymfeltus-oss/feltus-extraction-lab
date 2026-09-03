# Update the existing FELTUS Extraction Lab without losing documents

This update changes folder ingestion so every unique source path is accepted and adds working bulk categorization. Same-named and byte-identical PDFs in different folders are valid separate evidence records.

The package does not contain a `data` directory. Extracting it over the existing application updates the code while preserving the existing database and uploaded PDFs.

## Apply the update

1. In the running Extraction Lab PowerShell window, press `Ctrl+C`.

2. Open a new PowerShell window and run:

```powershell
$App = "C:\Users\kymfe\Downloads\FELTUS_Universal_Evidence_Lab_Current"
$Zip = Get-ChildItem "C:\Users\kymfe\Downloads" `
  -Filter "FELTUS_Universal_Evidence_Lab_Current*.zip" |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 1

if (-not $Zip) {
  throw "The updated FELTUS_Universal_Evidence_Lab_Current ZIP was not found in Downloads."
}

Expand-Archive -Path $Zip.FullName -DestinationPath $App -Force
Set-Location $App
powershell -ExecutionPolicy Bypass -File .\start-windows.ps1
```

3. Open `http://localhost:8000` and refresh the browser with `Ctrl+F5`.

4. Select the original evidence folder again.

The browser compares its selected source paths with the dashboard. The existing 383 paths are skipped, and only missing paths are submitted. Files with the same filename or identical bytes are accepted when their folder paths differ.

## Categorize selected files

The dashboard now provides:

- A checkbox on every document row
- A header checkbox to select every document currently visible after filtering or searching
- **Select all files** to select the complete dashboard inventory
- **Clear selection**
- Standard and custom category selection
- **Clear category** for selected documents
- **Apply category to selected**

The bulk save is one atomic database operation. Either every selected document is updated or none are. The dashboard totals refresh immediately, and every document retains its immutable category-assignment history.
