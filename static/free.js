const freeState = { file: null, run: null };

const free$ = selector => document.querySelector(selector);

async function freeRequest(url, options = {}) {
  const response = await fetch(url, { credentials: "include", ...options });
  if (response.ok) return response.json();

  let detail = "Request failed. Please try again.";
  try {
    const body = await response.json();
    detail = typeof body.detail === "string" ? body.detail : body.detail?.message || detail;
  } catch {}

  if (response.status === 401) {
    window.location.replace("/app?plan=free_trial&next=free");
    throw new Error("Sign in required.");
  }

  if (response.status === 402) {
    window.location.assign("/pricing?upgrade=free-limit");
    throw new Error("An upgrade is required to continue.");
  }

  throw new Error(detail);
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
  extract.disabled = !freeState.file;
  if (freeState.file) filename.textContent = `${freeState.file.name} · ${formatFileSize(freeState.file.size)}`;
}

function selectFreeFile(file) {
  if (!file) return;
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
    const upload = await freeRequest("/api/documents", { method: "POST", body: data });
    const run = await freeRequest(`/api/runs/${encodeURIComponent(upload.run_id)}`);
    showRawResult(run);
    clearFreeFile(false);
    setFreeStatus("Extraction complete.");
  } catch (error) {
    setFreeStatus(error.message || "The PDF could not be extracted.", true);
  } finally {
    button.textContent = "Extract raw data";
    button.disabled = !freeState.file;
  }
}

async function initializeFreeWorkspace() {
  try {
    await freeRequest("/api/auth/me");
    document.title = `Free PDF Extraction · ${window.BRANDING?.brandName || "FELTUS"}`;
    free$("#free-app").hidden = false;
    free$("#free-loading").hidden = true;
  } catch {
    // freeRequest sends unauthenticated visitors through the sign-in flow.
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
free$("#free-logout").addEventListener("click", async () => {
  try { await fetch("/api/auth/logout", { method: "POST", credentials: "include" }); } catch {}
  window.location.replace("/");
});

initializeFreeWorkspace();
