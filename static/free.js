const freeState = { file: null, run: null, available: true };

const free$ = selector => document.querySelector(selector);

async function freeRequest(url, options = {}) {
  const response = await fetch(url, { credentials: "include", ...options });
  if (response.ok) return response.json();

  let detail = "Request failed. Please try again.";
  try {
    const body = await response.json();
    detail = typeof body.detail === "string" ? body.detail : body.detail?.message || detail;
  } catch {}

  const error = new Error(detail);
  error.status = response.status;
  throw error;
}

function formatFileSize(bytes) {
  if (!Number.isFinite(bytes)) return "";
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function setFreeStatus(message = "", error = false) {
  const status = free$("#free-upload-status");
  status.textContent = message;
  status.classList.toggle("error", error);
}

function renderSelectedFile() {
  const selection = free$("#free-selection");
  const filename = free$("#free-selected-file");
  const extract = free$("#free-extract");
  selection.hidden = !freeState.file;
  extract.disabled = !freeState.file || !freeState.available;
  if (freeState.file) filename.textContent = `${freeState.file.name} · ${formatFileSize(freeState.file.size)}`;
}

function setFreeAvailability(available) {
  freeState.available = available;
  const dropZone = free$("#free-drop-zone");
  const input = free$("#free-pdf-input");
  dropZone.classList.toggle("unavailable", !available);
  dropZone.setAttribute("aria-disabled", String(!available));
  input.disabled = !available;
  renderSelectedFile();
}

function selectFreeFile(file) {
  if (!file || !freeState.available) return;
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    setFreeStatus("Choose a PDF file to continue.", true);
    return;
  }
  const maxBytes = ((window.BRANDING?.maxUploadMB || 250) * 1024 * 1024);
  if (file.size > maxBytes) {
    setFreeStatus(`This PDF exceeds the ${(maxBytes / 1024 / 1024).toFixed(0)} MB limit.`, true);
    return;
  }
  freeState.file = file;
  setFreeStatus("");
  renderSelectedFile();
}

function clearFreeFile(clearStatus = true) {
  freeState.file = null;
  free$("#free-pdf-input").value = "";
  renderSelectedFile();
  if (clearStatus) setFreeStatus("");
}

function showRawResult(run) {
  freeState.run = run;
  const rawText = run.raw_text || "No text was extracted from this PDF.";
  free$("#free-raw-output").textContent = rawText;
  free$("#free-result-meta").textContent = `${run.original_filename} · ${run.page_count || 0} page${run.page_count === 1 ? "" : "s"}${run.ocr_used ? " · OCR used" : ""}`;
  free$("#free-copy-status").textContent = "";
  free$("#free-result").hidden = false;
  free$("#free-result").scrollIntoView({ behavior: "smooth", block: "start" });
}

async function copyRawData() {
  const text = freeState.run?.raw_text || "";
  const status = free$("#free-copy-status");
  if (!text) {
    status.textContent = "There is no extracted text to copy.";
    return;
  }
  try {
    await navigator.clipboard.writeText(text);
    status.textContent = "Raw data copied to your clipboard.";
  } catch {
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    const copied = document.execCommand("copy");
    textarea.remove();
    status.textContent = copied ? "Raw data copied to your clipboard." : "Copy failed. Select the raw data and copy it manually.";
  }
}

function downloadRawData() {
  const text = freeState.run?.raw_text || "";
  if (!text) return;
  const sourceName = freeState.run?.original_filename || "extraction";
  const filename = `${sourceName.replace(/\.pdf$/i, "") || "extraction"}-raw-data.txt`;
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

async function extractFreeFile(event) {
  event.preventDefault();
  if (!freeState.file) return;

  const button = free$("#free-extract");
  button.disabled = true;
  button.textContent = "Extracting raw data…";
  setFreeStatus("Uploading and extracting your PDF…");

  try {
    const data = new FormData();
    data.append("file", freeState.file, freeState.file.name);
    data.append("relative_path", freeState.file.name);
    const result = await freeRequest("/api/free/extract", { method: "POST", body: data });
    showRawResult(result);
    clearFreeFile(false);
    setFreeAvailability(false);
    setFreeStatus("Your free extraction is complete. Download the raw text below.");
  } catch (error) {
    if (error.status === 409) setFreeAvailability(false);
    setFreeStatus(error.message || "The PDF could not be extracted.", true);
  } finally {
    button.textContent = "Extract raw data";
    button.disabled = !freeState.file || !freeState.available;
  }
}

async function initializeFreeWorkspace() {
  document.title = `Free PDF Extraction · ${window.BRANDING?.brandName || "FELTUS"}`;
  free$("#free-app").hidden = false;
  free$("#free-loading").hidden = true;
  try {
    const status = await freeRequest("/api/free/status");
    setFreeAvailability(status.available);
    if (!status.available) {
      setFreeStatus(
        "The free extraction has already been used from this IP address. Choose a membership to continue.",
        true
      );
    }
  } catch (error) {
    setFreeAvailability(false);
    setFreeStatus(error.message || "The free extraction service is unavailable.", true);
  }
}

const freeInput = free$("#free-pdf-input");
const freeDropZone = free$("#free-drop-zone");

free$("#free-choose-file").addEventListener("click", event => {
  event.stopPropagation();
  freeInput.click();
});
freeDropZone.addEventListener("click", event => {
  if (!event.target.closest("button")) freeInput.click();
});
freeDropZone.addEventListener("keydown", event => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    freeInput.click();
  }
});
freeInput.addEventListener("change", () => {
  selectFreeFile(freeInput.files?.[0]);
  freeInput.value = "";
});
["dragenter", "dragover"].forEach(type => freeDropZone.addEventListener(type, event => {
  event.preventDefault();
  freeDropZone.classList.add("dragging");
}));
["dragleave", "drop"].forEach(type => freeDropZone.addEventListener(type, event => {
  event.preventDefault();
  freeDropZone.classList.remove("dragging");
}));
freeDropZone.addEventListener("drop", event => selectFreeFile(event.dataTransfer.files?.[0]));
free$("#free-clear-file").addEventListener("click", clearFreeFile);
free$("#free-upload-form").addEventListener("submit", extractFreeFile);
free$("#free-copy").addEventListener("click", copyRawData);
free$("#free-download").addEventListener("click", downloadRawData);

initializeFreeWorkspace();
