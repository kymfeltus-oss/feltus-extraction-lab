const MAX_FILE_BYTES = (typeof BRANDING !== 'undefined' ? BRANDING.maxUploadMB : 250) * 1024 * 1024;
const state = {runs: [], current: null, selected: [], skipped: 0, filter: 'all'};
let isAuthenticated = false;
const $ = selector => document.querySelector(selector);
const esc = value => String(value ?? "—").replace(/[&<>"']/g, character => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[character]));

function setAuthenticated(value, me = null) {
  isAuthenticated = value;
  $("#login-screen").hidden = value;
  $("#app-main").hidden = !value;
  $("#logout-button").hidden = !value;
  if (value) {
    if (typeof BRANDING !== 'undefined') {
      $("#max-upload-size").textContent = BRANDING.maxUploadMB;
      $("#max-upload-size-card").textContent = BRANDING.maxUploadMB;
    }
    loadDashboard();
    loadRuns();
  }
}

async function checkAuth() {
  try {
    const me = await api("/api/auth/me");
    setAuthenticated(true, me);
  } catch {
    setAuthenticated(false);
  }
}

async function login(event) {
  event.preventDefault();
  const email = $("#login-email").value;
  const password = $("#login-password").value;
  const errorEl = $("#login-error");
  errorEl.hidden = true;
  try {
    const result = await api("/api/auth/login", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({email, password})
    });
    setAuthenticated(true, result);
  } catch (e) {
    errorEl.textContent = e.message || "Login failed";
    errorEl.hidden = false;
  }
}

async function logout() {
  try {
    await api("/api/auth/logout", {method: "POST"});
  } catch {}
  state.runs = [];
  state.current = null;
  state.selected = [];
  state.skipped = 0;
  $("#run-list").innerHTML = '<p class="empty">No extraction runs yet.</p>';
  $("#metric-total-uploads").textContent = "0";
  $("#metric-total-extracted").textContent = "0";
  $("#metric-total-rejected").textContent = "0";
  setAuthenticated(false);
}

async function api(url, options) {
  const config = {credentials: "include", ...options};
  const response = await fetch(url, config);
  if (!response.ok) {
    let detail = "Request failed";
    try { detail = (await response.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return response.json();
}

function pct(value) { return `${Math.round((value || 0) * 100)}%`; }
function stamp(value) { return value ? new Date(value).toLocaleString() : "In progress"; }

async function checkHealth() {
  try {
    const health = await api("/health");
    $("#health").classList.add("ok");
    $("#health").lastChild.textContent = ` Service ready · ${health.max_upload_mb} MB per PDF`;
  } catch {
    $("#health").lastChild.textContent = " Service unavailable";
  }
}

async function loadDashboard() {
  try {
    const dashboard = await api("/api/dashboard");
    $("#metric-total-uploads").textContent = dashboard.total_uploads;
    $("#metric-total-extracted").textContent = dashboard.total_extracted;
    $("#metric-total-rejected").textContent = dashboard.total_rejected;
    setFilter(state.filter, false);
  } catch {
    // Dashboard data not available, keep zeros
  }
}

function filterRuns(runs) {
  if (state.filter === 'all') return runs;
  if (state.filter === 'extracted') return runs.filter(run => run.status === 'VALIDATED');
  if (state.filter === 'rejected') return runs.filter(run => run.latest_review === 'REJECTED');
  return runs;
}

function renderRuns(selectId) {
  const list = $("#run-list");
  const filtered = filterRuns(state.runs);
  const titles = {all: 'Extraction runs', extracted: 'Extracted documents', rejected: 'Rejected documents'};
  $("#run-history-title").textContent = titles[state.filter] || 'Extraction runs';
  if (!filtered.length) {
    list.innerHTML = '<p class="empty">No runs match the selected filter.</p>';
    return;
  }
  list.innerHTML = filtered.map(run => {
    const sourcePath = run.source_relative_path || run.original_filename;
    const docType = (run.document_type || 'PDF_DOCUMENT').replaceAll("_", " ");
    return `<button class="run-item ${run.id === selectId ? "active" : ""}" data-run="${run.id}">
      <strong title="${esc(sourcePath)}">${esc(sourcePath)}</strong>
      <span><b>${esc(docType)}</b><time>${stamp(run.completed_at)}</time></span>
      <span class="status-pill ${run.status.toLowerCase()}">${esc(run.status.replaceAll("_", " "))}</span>
    </button>`;
  }).join("");
  list.querySelectorAll("[data-run]").forEach(button => { button.onclick = () => openRun(button.dataset.run); });
}

async function loadRuns(selectId) {
  state.runs = await api("/api/runs");
  renderRuns(selectId);
}

function setFilter(filter, render = true) {
  if (state.filter === filter && filter !== 'all') {
    state.filter = 'all';
  } else {
    state.filter = filter;
  }
  document.querySelectorAll(".metric-card").forEach(card => {
    card.classList.toggle("active", card.dataset.filter === state.filter);
  });
  if (render) renderRuns(state.current?.id);
}

function summary(data, warnings, errors) {
  const groups = [];
  const skip = new Set(["schema_version", "document_type", "classification_evidence", "transactions", "reconciliation", "source_extraction"]);
  const source = data.source_extraction;
  if (source) {
    groups.push(`<div class="summary-group"><h4>Page extraction</h4><div class="field-row"><span>Total PDF pages</span><strong>${source.total_pages}</strong></div><div class="field-row"><span>Pages with text</span><strong>${source.pages_with_text}</strong></div><div class="field-row"><span>Pages needing review</span><strong>${source.pages_needing_review}</strong></div>${source.page_results.map(page => `<div class="field-row"><span>Page ${page.page_number}</span><strong>${esc(page.method.replaceAll("_", " "))} · ${page.character_count.toLocaleString()} characters ${page.needs_review ? "· REVIEW REQUIRED" : ""}</strong></div>`).join("")}</div>`);
  }
  for (const [key, value] of Object.entries(data)) {
    if (skip.has(key)) continue;
    if (value && typeof value === "object" && !Array.isArray(value)) {
      groups.push(`<div class="summary-group"><h4>${esc(key.replaceAll("_", " "))}</h4>${Object.entries(value).map(([nestedKey, nestedValue]) => `<div class="field-row"><span>${esc(nestedKey.replaceAll("_", " "))}</span><strong>${esc(nestedValue)}</strong></div>`).join("")}</div>`);
    } else groups.push(`<div class="field-row"><span>${esc(key.replaceAll("_", " "))}</span><strong>${esc(value)}</strong></div>`);
  }
  if (data.transactions) groups.push(`<div class="summary-group"><h4>Transactions (${data.transactions.length})</h4>${data.transactions.slice(0, 50).map(transaction => `<div class="field-row"><span>${esc(transaction.date)} · ${esc(transaction.direction)}</span><strong>${esc(transaction.description)} · $${Number(transaction.amount).toLocaleString(undefined, {minimumFractionDigits: 2})}</strong></div>`).join("")}${data.transactions.length > 50 ? "<p>Showing first 50. Export JSON for all rows.</p>" : ""}</div>`);
  if (data.reconciliation) groups.push(`<div class="summary-group"><h4>Reconciliation</h4>${Object.entries(data.reconciliation).map(([key, value]) => `<div class="field-row"><span>${esc(key.replaceAll("_", " "))}</span><strong>${esc(value)}</strong></div>`).join("")}</div>`);
  if (warnings.length || errors.length) groups.push(`<div class="warning-list">${[...errors, ...warnings].map(warning => `<p>${esc(warning)}</p>`).join("")}</div>`);
  return groups.join("");
}

async function openRun(id) {
  const run = await api(`/api/runs/${id}`);
  state.current = run;
  const panel = $("#review-panel");
  panel.className = "review-panel";
  panel.innerHTML = "";
  panel.append($("#review-template").content.cloneNode(true));
  $("#run-title").textContent = run.original_filename;
  const sourcePath = run.source_relative_path || run.original_filename;
  $("#run-path").textContent = sourcePath !== run.original_filename ? `Source folder: ${sourcePath}` : "";
  $("#run-meta").textContent = `Run ${run.id.slice(0, 8)} · ${run.page_count} pages · ${stamp(run.completed_at)}`;
  $("#metric-type").textContent = run.document_type.replaceAll("_", " ");
  $("#metric-classification").textContent = pct(run.classification_confidence);
  $("#metric-extraction").textContent = pct(run.extraction_confidence);
  $("#metric-ocr").textContent = run.ocr_used ? "Used" : "Not needed";
  const strip = $("#status-strip");
  strip.textContent = run.status === "VALIDATED" ? "Every PDF page produced extractable text. Step 1 transcription is complete." : run.errors.length ? run.errors.join(" ") : "One or more pages need OCR or professional review before Step 1 is complete.";
  strip.className = `status-strip ${run.status === "VALIDATED" ? "good" : run.status === "FAILED" ? "bad" : ""}`;
  const pdfUrl = `/api/documents/${run.document_id}/file`;
  $("#pdf-viewer").src = pdfUrl;
  $("#open-pdf").href = pdfUrl;
  $("#export").href = `/api/runs/${run.id}/json`;
  $("#schema-version").textContent = `Schema ${run.normalized.schema_version || "—"}`;
  $("#result-summary").innerHTML = summary(run.normalized, run.warnings, run.errors);
  $("#json-output").textContent = JSON.stringify(run.normalized, null, 2);
  $("#raw-output").textContent = run.raw_text || "No text extracted.";
  $("#provenance-body").innerHTML = run.fields.map(field => `<tr><td>${esc(field.field_path)}</td><td>${esc(typeof field.value === "object" ? JSON.stringify(field.value) : field.value)}</td><td>${esc(field.page_number)}</td><td>${pct(field.confidence)}</td><td>${esc(field.source_text)}</td></tr>`).join("") || '<tr><td colspan="5">No field-level provenance was created.</td></tr>';
  $("#review-history").innerHTML = run.reviews.map(review => `<div class="review-record"><strong>${esc(review.decision.replaceAll("_", " "))}</strong><span>${stamp(review.reviewed_at)} · ${esc(review.note || "No note")}</span></div>`).join("");
  document.querySelectorAll(".tabs button").forEach(button => { button.onclick = () => { document.querySelectorAll(".tabs button,.tab-pane").forEach(element => element.classList.remove("active")); button.classList.add("active"); $(`#tab-${button.dataset.tab}`).classList.add("active"); }; });
  $("#rerun").onclick = rerun;
  document.querySelectorAll("[data-decision]").forEach(button => { button.onclick = () => review(button.dataset.decision); });
  renderRuns(id);
}

async function rerun() {
  const button = $("#rerun");
  button.disabled = true;
  button.textContent = "Extracting…";
  try {
    const result = await api(`/api/documents/${state.current.document_id}/runs`, {method: "POST"});
    await loadRuns(result.run_id);
    await openRun(result.run_id);
    await loadDashboard();
  } catch (error) { alert(error.message); }
  finally { button.disabled = false; button.textContent = "Run Again"; }
}

async function review(decision) {
  const note = $("#review-note").value;
  try {
    await api(`/api/runs/${state.current.id}/reviews`, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({decision, note})});
    await openRun(state.current.id);
    await loadDashboard();
  } catch (error) { alert(error.message); }
}

function addSelections(items, skipped = 0) {
  const message = $("#upload-message");
  message.className = "";
  message.textContent = "";
  state.skipped += skipped;
  const existing = new Set(state.selected.map(item => `${item.relativePath}|${item.file.size}|${item.file.lastModified}`));
  for (const item of items) {
    if (!item.file.name.toLowerCase().endsWith(".pdf")) { state.skipped += 1; continue; }
    const key = `${item.relativePath}|${item.file.size}|${item.file.lastModified}`;
    if (!existing.has(key)) { state.selected.push(item); existing.add(key); }
  }
  renderSelection();
}

function renderSelection() {
  const count = state.selected.length;
  const skippedText = state.skipped ? ` · ${state.skipped} non-PDF ${state.skipped === 1 ? "file" : "files"} skipped` : "";
  $("#selected-file").textContent = count ? `${count} PDF ${count === 1 ? "file" : "files"} selected${skippedText}` : `No PDFs selected${skippedText}`;
  $("#upload-button").disabled = count === 0;
  $("#clear-selection").hidden = count === 0 && state.skipped === 0;
}

function clearSelection() {
  state.selected = [];
  state.skipped = 0;
  $("#pdf-files").value = "";
  $("#pdf-folder").value = "";
  renderSelection();
}

function readDirectoryEntries(reader) {
  return new Promise((resolve, reject) => {
    const entries = [];
    const readBatch = () => reader.readEntries(batch => { if (!batch.length) resolve(entries); else { entries.push(...batch); readBatch(); } }, reject);
    readBatch();
  });
}

async function walkEntry(entry, parentPath = "") {
  if (entry.isFile) {
    const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
    return [{file, relativePath: `${parentPath}${file.name}`}];
  }
  if (!entry.isDirectory) return [];
  const children = await readDirectoryEntries(entry.createReader());
  return (await Promise.all(children.map(child => walkEntry(child, `${parentPath}${entry.name}/`)))).flat();
}

async function selectionsFromDrop(dataTransfer) {
  const entries = Array.from(dataTransfer.items || []).map(item => item.webkitGetAsEntry?.()).filter(Boolean);
  if (entries.length) return (await Promise.all(entries.map(entry => walkEntry(entry)))).flat();
  return Array.from(dataTransfer.files || []).map(file => ({file, relativePath: file.webkitRelativePath || file.name}));
}

const filesInput = $("#pdf-files");
const folderInput = $("#pdf-folder");
const zone = $("#drop-zone");
$("#choose-files").onclick = event => { event.stopPropagation(); filesInput.click(); };
$("#choose-folder").onclick = event => { event.stopPropagation(); folderInput.click(); };
$("#clear-selection").onclick = event => { event.stopPropagation(); clearSelection(); };
zone.onclick = event => { if (!event.target.closest("button")) filesInput.click(); };
zone.onkeydown = event => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); filesInput.click(); } };
filesInput.onchange = () => { addSelections(Array.from(filesInput.files).map(file => ({file, relativePath: file.name}))); filesInput.value = ""; };
folderInput.onchange = () => { addSelections(Array.from(folderInput.files).map(file => ({file, relativePath: file.webkitRelativePath || file.name}))); folderInput.value = ""; };
["dragenter", "dragover"].forEach(eventName => zone.addEventListener(eventName, event => { event.preventDefault(); zone.classList.add("dragging"); }));
["dragleave", "drop"].forEach(eventName => zone.addEventListener(eventName, event => { event.preventDefault(); zone.classList.remove("dragging"); }));
zone.addEventListener("drop", async event => {
  const message = $("#upload-message");
  message.className = "";
  message.textContent = "Reading dropped files and folders…";
  try { addSelections(await selectionsFromDrop(event.dataTransfer)); message.textContent = ""; }
  catch (error) { message.textContent = `Could not read the dropped folder: ${error.message}`; }
});

$("#upload-form").onsubmit = async event => {
  event.preventDefault();
  if (!state.selected.length) return;
  const button = $("#upload-button");
  const message = $("#upload-message");
  const queue = [...state.selected];
  const failures = [];
  const skipped = state.skipped;
  let completed = 0;
  let lastRunId = null;
  button.disabled = true;
  message.className = "batch-summary";
  for (let index = 0; index < queue.length; index += 1) {
    const item = queue[index];
    button.textContent = `Extracting ${index + 1} of ${queue.length}…`;
    message.textContent = `Processing ${item.relativePath}`;
    if (item.file.size > MAX_FILE_BYTES) { failures.push(`${item.relativePath}: exceeds 250 MB`); continue; }
    const data = new FormData();
    data.append("file", item.file, item.file.name);
    data.append("relative_path", item.relativePath);
    try {
      const result = await api("/api/documents", {method: "POST", body: data});
      completed += 1;
      lastRunId = result.run_id;
    } catch (error) { failures.push(`${item.relativePath}: ${error.message}`); }
  }
  clearSelection();
  const problems = failures.length + skipped;
  message.className = `batch-summary${problems ? " error" : ""}`;
  message.textContent = `${completed} of ${queue.length} PDFs processed${failures.length ? ` · ${failures.length} failed: ${failures.slice(0, 3).join(" | ")}` : ""}${skipped ? ` · ${skipped} non-PDF files skipped` : ""}`;
  button.textContent = "Extract Selected PDFs";
  button.disabled = true;
  if (lastRunId) { await loadRuns(lastRunId); await openRun(lastRunId); }
  else await loadRuns();
  await loadDashboard();
};

document.querySelectorAll(".metric-card").forEach(card => {
  card.onclick = () => setFilter(card.dataset.filter);
});
$("#refresh").onclick = () => { if (isAuthenticated) { loadRuns(state.current?.id); loadDashboard(); } };
$("#login-form").onsubmit = login;
$("#logout-button").onclick = logout;
checkHealth();
checkAuth();
