const freeState = {
  file: null,
  run: null,
  available: true,
  pageLimit: 20,
  pdf: null,
  pageNumber: 1,
  zoom: 1,
  objectUrl: "",
  renderTask: null,
  selectionToken: 0,
};

const free$ = selector => document.querySelector(selector);

if (window.pdfjsLib) {
  window.pdfjsLib.GlobalWorkerOptions.workerSrc =
    "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
}

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
  extract.disabled = !freeState.file || !freeState.pdf || !freeState.available;
  if (freeState.file) {
    filename.textContent = `${freeState.file.name} · ${formatFileSize(freeState.file.size)}`;
  }
}

function setFreeAvailability(available) {
  freeState.available = available;
  const dropZone = free$("#free-drop-zone");
  const input = free$("#free-pdf-input");
  dropZone.classList.toggle("unavailable", !available);
  dropZone.setAttribute("aria-disabled", String(!available));
  input.disabled = !available;
  free$("#free-clear-file").disabled = !available;
  renderSelectedFile();
}

function updatePreviewControls() {
  const hasPdf = Boolean(freeState.pdf);
  const count = freeState.pdf?.numPages || 0;
  free$("#free-page-count").textContent = String(count);
  free$("#free-page-number").value = String(freeState.pageNumber);
  free$("#free-page-number").max = String(Math.max(1, count));
  free$("#free-page-number").disabled = !hasPdf;
  free$("#free-prev-page").disabled = !hasPdf || freeState.pageNumber <= 1;
  free$("#free-next-page").disabled = !hasPdf || freeState.pageNumber >= count;
  free$("#free-zoom-out").disabled = !hasPdf || freeState.zoom <= 0.5;
  free$("#free-zoom-in").disabled = !hasPdf || freeState.zoom >= 2.5;
  free$("#free-fit-width").disabled = !hasPdf;
  free$("#free-zoom-value").textContent = `${Math.round(freeState.zoom * 100)}%`;
}

async function renderPdfPage() {
  if (!freeState.pdf) return;
  if (freeState.renderTask) {
    freeState.renderTask.cancel();
    try {
      await freeState.renderTask.promise;
    } catch {}
  }

  const page = await freeState.pdf.getPage(freeState.pageNumber);
  const canvas = free$("#free-pdf-canvas");
  const context = canvas.getContext("2d", { alpha: false });
  const baseViewport = page.getViewport({ scale: 1 });
  const stageWidth = Math.max(240, free$("#free-preview-stage").clientWidth - 34);
  const fitScale = Math.min(2.5, stageWidth / baseViewport.width);
  const viewport = page.getViewport({ scale: fitScale * freeState.zoom });
  const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);

  canvas.width = Math.floor(viewport.width * pixelRatio);
  canvas.height = Math.floor(viewport.height * pixelRatio);
  canvas.style.width = `${Math.floor(viewport.width)}px`;
  canvas.style.height = `${Math.floor(viewport.height)}px`;

  freeState.renderTask = page.render({
    canvasContext: context,
    viewport,
    transform: pixelRatio === 1 ? null : [pixelRatio, 0, 0, pixelRatio, 0, 0],
  });
  try {
    await freeState.renderTask.promise;
  } catch (error) {
    if (error?.name !== "RenderingCancelledException") throw error;
  } finally {
    freeState.renderTask = null;
  }
  updatePreviewControls();
}

function resetPdfPreview() {
  freeState.selectionToken += 1;
  if (freeState.renderTask) freeState.renderTask.cancel();
  if (freeState.pdf) freeState.pdf.destroy();
  if (freeState.objectUrl) URL.revokeObjectURL(freeState.objectUrl);
  freeState.pdf = null;
  freeState.pageNumber = 1;
  freeState.zoom = 1;
  freeState.objectUrl = "";
  free$("#free-pdf-canvas").width = 0;
  free$("#free-preview-empty").hidden = false;
  free$("#free-preview-canvas-wrap").hidden = true;
  free$("#free-open-pdf").hidden = true;
  updatePreviewControls();
}

async function loadPdfPreview(file, token) {
  if (!window.pdfjsLib) {
    throw new Error("The PDF preview could not load. Refresh the page and try again.");
  }
  const data = new Uint8Array(await file.arrayBuffer());
  const pdf = await window.pdfjsLib.getDocument({ data }).promise;
  if (token !== freeState.selectionToken) {
    pdf.destroy();
    return;
  }
  if (pdf.numPages > freeState.pageLimit) {
    pdf.destroy();
    throw new Error(`The free workspace accepts PDFs with up to ${freeState.pageLimit} pages.`);
  }

  freeState.pdf = pdf;
  freeState.objectUrl = URL.createObjectURL(file);
  free$("#free-open-pdf").href = freeState.objectUrl;
  free$("#free-open-pdf").hidden = false;
  free$("#free-preview-empty").hidden = true;
  free$("#free-preview-canvas-wrap").hidden = false;
  updatePreviewControls();
  await renderPdfPage();
}

async function selectFreeFile(file) {
  if (!file || !freeState.available) return;
  if (!file.name.toLowerCase().endsWith(".pdf")) {
    setFreeStatus("Choose a PDF file to continue.", true);
    return;
  }
  const maxBytes = (window.BRANDING?.maxUploadMB || 250) * 1024 * 1024;
  if (file.size > maxBytes) {
    setFreeStatus(`This PDF exceeds the ${(maxBytes / 1024 / 1024).toFixed(0)} MB limit.`, true);
    return;
  }

  resetPdfPreview();
  const token = freeState.selectionToken;
  freeState.file = file;
  setFreeStatus("Loading PDF preview…");
  renderSelectedFile();
  try {
    await loadPdfPreview(file, token);
    setFreeStatus(`${freeState.pdf.numPages} page${freeState.pdf.numPages === 1 ? "" : "s"} ready to extract.`);
  } catch (error) {
    if (token !== freeState.selectionToken) return;
    freeState.file = null;
    resetPdfPreview();
    renderSelectedFile();
    setFreeStatus(error.message || "This PDF could not be previewed.", true);
  }
  renderSelectedFile();
}

function clearFreeFile(clearStatus = true) {
  if (!freeState.available) return;
  freeState.file = null;
  free$("#free-pdf-input").value = "";
  resetPdfPreview();
  renderSelectedFile();
  if (clearStatus) setFreeStatus(`PDF only · maximum ${freeState.pageLimit} pages`);
}

function showRawResult(run) {
  freeState.run = run;
  const rawText = run.raw_text || "No text was extracted from this PDF.";
  free$("#free-raw-output").textContent = rawText;
  free$("#free-raw-output").hidden = false;
  free$("#free-output-empty").hidden = true;
  free$("#free-result-actions").hidden = false;
  free$("#free-result-meta").textContent = `${run.original_filename} · ${run.page_count || 0} page${run.page_count === 1 ? "" : "s"}${run.ocr_used ? " · OCR used" : ""}`;
  free$("#free-copy-status").textContent = "";
  free$("#free-result-actions").scrollIntoView({ behavior: "smooth", block: "nearest" });
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
  if (!freeState.file || !freeState.pdf) return;

  const button = free$("#free-extract");
  button.disabled = true;
  button.textContent = "Extracting…";
  setFreeStatus("Uploading and extracting your PDF…");

  try {
    const data = new FormData();
    data.append("file", freeState.file, freeState.file.name);
    const result = await freeRequest("/api/free/extract", { method: "POST", body: data });
    showRawResult(result);
    setFreeAvailability(false);
    setFreeStatus("Extraction complete. Copy or download the raw text.");
  } catch (error) {
    if (error.status === 409) setFreeAvailability(false);
    setFreeStatus(error.message || "The PDF could not be extracted.", true);
  } finally {
    button.textContent = "Extract raw data";
    button.disabled = !freeState.file || !freeState.pdf || !freeState.available;
  }
}

async function initializeFreeWorkspace() {
  document.title = `Free PDF Extraction · ${window.BRANDING?.brandName || "FELTUS"}`;
  free$("#free-app").hidden = false;
  free$("#free-loading").hidden = true;
  updatePreviewControls();
  try {
    const status = await freeRequest("/api/free/status");
    freeState.pageLimit = status.page_limit || 20;
    setFreeAvailability(status.available);
    setFreeStatus(
      status.available
        ? `PDF only · maximum ${freeState.pageLimit} pages`
        : "The one-time free extraction has already been used from this IP address.",
      !status.available
    );
  } catch (error) {
    setFreeAvailability(false);
    setFreeStatus(error.message || "The free extraction service is unavailable.", true);
  }
}

const freeInput = free$("#free-pdf-input");
const freeDropZone = free$("#free-drop-zone");

free$("#free-choose-file").addEventListener("click", event => {
  event.stopPropagation();
  if (freeState.available) freeInput.click();
});
freeDropZone.addEventListener("click", event => {
  if (freeState.available && !event.target.closest("button")) freeInput.click();
});
freeDropZone.addEventListener("keydown", event => {
  if (freeState.available && (event.key === "Enter" || event.key === " ")) {
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
  if (freeState.available) freeDropZone.classList.add("dragging");
}));
["dragleave", "drop"].forEach(type => freeDropZone.addEventListener(type, event => {
  event.preventDefault();
  freeDropZone.classList.remove("dragging");
}));
freeDropZone.addEventListener("drop", event => selectFreeFile(event.dataTransfer.files?.[0]));
free$("#free-clear-file").addEventListener("click", () => clearFreeFile());
free$("#free-upload-form").addEventListener("submit", extractFreeFile);
free$("#free-copy").addEventListener("click", copyRawData);
free$("#free-download").addEventListener("click", downloadRawData);
free$("#free-prev-page").addEventListener("click", async () => {
  if (freeState.pageNumber <= 1) return;
  freeState.pageNumber -= 1;
  await renderPdfPage();
});
free$("#free-next-page").addEventListener("click", async () => {
  if (!freeState.pdf || freeState.pageNumber >= freeState.pdf.numPages) return;
  freeState.pageNumber += 1;
  await renderPdfPage();
});
free$("#free-page-number").addEventListener("change", async event => {
  if (!freeState.pdf) return;
  freeState.pageNumber = Math.min(freeState.pdf.numPages, Math.max(1, Number(event.target.value) || 1));
  await renderPdfPage();
});
free$("#free-zoom-out").addEventListener("click", async () => {
  freeState.zoom = Math.max(0.5, Number((freeState.zoom - 0.1).toFixed(1)));
  await renderPdfPage();
});
free$("#free-zoom-in").addEventListener("click", async () => {
  freeState.zoom = Math.min(2.5, Number((freeState.zoom + 0.1).toFixed(1)));
  await renderPdfPage();
});
free$("#free-fit-width").addEventListener("click", async () => {
  freeState.zoom = 1;
  await renderPdfPage();
});

let resizeTimer;
window.addEventListener("resize", () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    if (freeState.pdf) renderPdfPage();
  }, 120);
});
window.addEventListener("beforeunload", () => {
  if (freeState.objectUrl) URL.revokeObjectURL(freeState.objectUrl);
});

initializeFreeWorkspace();
