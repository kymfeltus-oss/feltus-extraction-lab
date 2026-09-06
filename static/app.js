const MAX_FILE_BYTES = (typeof BRANDING !== 'undefined' ? BRANDING.maxUploadMB : 250) * 1024 * 1024;
const state = {runs: [], current: null, selected: [], skipped: 0, filter: 'all'};
let isAuthenticated = false;
const $ = selector => document.querySelector(selector);
const esc = value => String(value ?? "—").replace(/[&<>"']/g, character => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[character]));

const pdfState = {
  pdf: null,
  pageNum: 1,
  pageCount: 1,
  scale: 1,
  mode: "fit-page",
  renderTask: null,
  url: null
};

if (typeof pdfjsLib !== "undefined") {
  pdfjsLib.GlobalWorkerOptions.workerSrc = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
}

let pdfResizeTimer = null;
window.addEventListener("resize", () => {
  if (!pdfState.pdf) return;
  clearTimeout(pdfResizeTimer);
  pdfResizeTimer = setTimeout(() => {
    renderPdfPage(pdfState.pageNum);
  }, 200);
});

function resetLoginButton() {
  const button = $("#login-button");
  if (!button) return;
  const buttonText = button.querySelector(".button-text");
  const spinner = button.querySelector(".spinner");
  const arrow = button.querySelector(".arrow");
  if (buttonText) buttonText.textContent = "Sign In";
  if (spinner) spinner.hidden = true;
  if (arrow) arrow.hidden = false;
  button.disabled = false;
  button.removeAttribute("aria-busy");
}

function setLoginLoading(loading) {
  const button = $("#login-button");
  if (!button) return;
  const buttonText = button.querySelector(".button-text");
  const spinner = button.querySelector(".spinner");
  const arrow = button.querySelector(".arrow");
  if (loading) {
    if (buttonText) buttonText.textContent = "Signing In…";
    if (spinner) spinner.hidden = false;
    if (arrow) arrow.hidden = true;
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
  } else {
    resetLoginButton();
  }
}

function setAuthenticated(value, me = null) {
  isAuthenticated = value;
  $("#login-screen").hidden = value;
  $("#app-main").hidden = !value;
  const topbar = $("#topbar");
  if (topbar) topbar.hidden = true;
  const logoutButton = $("#logout-button");
  if (logoutButton) logoutButton.hidden = !value;
  const errorEl = $("#login-error");
  if (errorEl) errorEl.hidden = true;
  if (!value) {
    clearSessionToken();
    resetLoginButton();
    const emailInput = $("#login-email");
    const passwordInput = $("#login-password");
    if (emailInput) emailInput.value = "";
    if (passwordInput) passwordInput.value = "";
    $("#greeting-name").textContent = "";
    $("#user-name").textContent = "";
    $("#user-initials").textContent = "";
    $("#user-role").textContent = "";
    renderOverview();
  }
  if (value) {
    if (typeof BRANDING !== 'undefined') {
      $("#max-upload-size").textContent = BRANDING.maxUploadMB;
      $("#max-upload-size-card").textContent = BRANDING.maxUploadMB;
      $("#max-upload-size-badge").textContent = BRANDING.maxUploadMB;
      const supportLink = $("#support-link");
      if (supportLink && BRANDING.supportEmail) supportLink.href = `mailto:${BRANDING.supportEmail}`;
    }
    if (me && me.user) {
      const firstName = me.user.name ? me.user.name.split(" ")[0] : "";
      $("#greeting-name").textContent = firstName;
      $("#user-name").textContent = me.user.name || "";
      $("#user-initials").textContent = getInitials(me.user.name);
      $("#user-role").textContent = me.active_organization?.role ? me.active_organization.role.toLowerCase() : "";
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
  } finally {
    const loader = $("#auth-loading");
    if (loader) loader.hidden = true;
  }
}

async function login(event) {
  event.preventDefault();
  const email = $("#login-email").value.trim();
  const password = $("#login-password").value;
  const errorEl = $("#login-error");
  if (errorEl) errorEl.hidden = true;
  if (!email || !password) {
    if (errorEl) {
      errorEl.textContent = "Please enter both email and password.";
      errorEl.hidden = false;
    }
    return;
  }
  clearSessionToken();
  setLoginLoading(true);
  try {
    const result = await api("/api/auth/login", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({email, password})
    });
    setAuthenticated(true, result);
  } catch (e) {
    if (errorEl) {
      errorEl.textContent = e.message || "Invalid email or password. Please try again.";
      errorEl.hidden = false;
    }
  } finally {
    setLoginLoading(false);
  }
}

async function logout() {
  try {
    await api("/api/auth/logout", {method: "POST"});
  } catch {}
  clearSessionToken();
  state.runs = [];
  state.current = null;
  state.selected = [];
  state.skipped = 0;
  state.filter = 'all';
  $("#run-list").innerHTML = '<p class="empty">No extraction runs yet.</p>';
  $("#metric-total-uploads").textContent = "0";
  $("#metric-total-extracted").textContent = "0";
  $("#metric-total-rejected").textContent = "0";
  renderOverview();
  setAuthenticated(false);
}

const AUTH_TOKEN_KEY = "feltus_access_token";

function getSessionToken() {
  try { return sessionStorage.getItem(AUTH_TOKEN_KEY); } catch { return null; }
}

function setSessionToken(token) {
  try { if (token) sessionStorage.setItem(AUTH_TOKEN_KEY, token); } catch {}
}

function clearSessionToken() {
  try { sessionStorage.removeItem(AUTH_TOKEN_KEY); } catch {}
}

async function api(url, options) {
  const token = getSessionToken();
  const baseHeaders = token ? {Authorization: `Bearer ${token}`} : {};
  const config = {
    credentials: "include",
    ...options,
    headers: {...baseHeaders, ...(options?.headers || {})}
  };
  const response = await fetch(url, config);
  if (!response.ok) {
    let detail = "Request failed";
    try { detail = (await response.json()).detail || detail; } catch {}
    throw new Error(detail);
  }
  return response.json();
}

async function handleAuthHash() {
  const hash = window.location.hash;
  if (!hash || hash.length < 2) return;

  const params = new URLSearchParams(hash.substring(1));
  const accessToken = params.get("access_token");
  const type = params.get("type") || "";

  if (!accessToken || !["signup", "magiclink", "recovery"].includes(type)) return;

  const refreshToken = params.get("refresh_token") || "";
  const expiresIn = parseInt(params.get("expires_in") || "3600", 10) || 3600;

  setSessionToken(accessToken);

  try {
    const result = await api("/api/auth/confirm", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        access_token: accessToken,
        refresh_token: refreshToken,
        expires_in: expiresIn
      })
    });

    const url = new URL(window.location.href);
    url.hash = "";
    history.replaceState({}, "", url);

    setAuthenticated(true, result);
  } catch (error) {
    console.error("FELTUS confirmation failed:", error);
    clearSessionToken();

    const url = new URL(window.location.href);
    url.hash = "";
    history.replaceState({}, "", url);
  }
}

function pct(value) { return `${Math.round((value || 0) * 100)}%`; }
function stamp(value) { return value ? new Date(value).toLocaleString() : "In progress"; }
function getInitials(name) {
  return (name || "KF").split(" ").filter(Boolean).slice(0, 2).map(p => p[0].toUpperCase()).join("") || "KF";
}

async function checkHealth() {
  try {
    const health = await api("/health");
    $("#health").classList.add("ok");
    $("#health").lastChild.textContent = " Service ready";
    if (health.max_upload_mb) {
      const maxUpload = $("#max-upload-size");
      if (maxUpload) maxUpload.textContent = health.max_upload_mb;
      const maxUploadCard = $("#max-upload-size-card");
      if (maxUploadCard) maxUploadCard.textContent = health.max_upload_mb;
      const maxUploadBadge = $("#max-upload-size-badge");
      if (maxUploadBadge) maxUploadBadge.textContent = health.max_upload_mb;
    }
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
  renderOverview();
}

function filterRuns(runs) {
  // The dashboard metrics count documents, not runs, so the drill-down
  // shows the latest run for each document, then filters by group.
  const latestByDocument = new Map();
  for (const run of runs) {
    if (!latestByDocument.has(run.document_id)) {
      latestByDocument.set(run.document_id, run);
    }
  }
  const latest = Array.from(latestByDocument.values());

  if (state.filter === 'all') return latest;
  if (state.filter === 'extracted') return latest.filter(run => run.status === 'VALIDATED');
  if (state.filter === 'rejected') return latest.filter(run => run.latest_review === 'REJECTED');
  return latest;
}

function updateRunHistoryHeader(count) {
  const title = $("#run-history-title");
  const countEl = $("#run-history-count");
  if (!title) return;
  const labels = {
    all: "RUN HISTORY",
    extracted: "EXTRACTED",
    rejected: "REJECTED"
  };
  title.textContent = labels[state.filter] || "RUN HISTORY";
  if (countEl) {
    if (state.filter === 'all') {
      countEl.hidden = true;
    } else {
      countEl.textContent = count;
      countEl.hidden = false;
    }
  }
}

function renderRuns(selectId) {
  const list = $("#run-list");
  const filtered = filterRuns(state.runs);
  updateRunHistoryHeader(filtered.length);
  if (!filtered.length) {
    list.innerHTML = state.runs.length && state.filter !== 'all'
      ? '<p class="empty">No runs match the selected filter.</p>'
      : '<p class="empty">No extraction runs yet.</p>';
    return;
  }
  list.innerHTML = filtered.map(run => {
    const sourcePath = run.source_relative_path || run.original_filename;
    const docType = (run.document_type || 'PDF_DOCUMENT').replaceAll("_", " ");
    const status = (run.status || 'PENDING').replaceAll("_", " ");
    const isRejected = run.latest_review === 'REJECTED' || run.status === 'FAILED';
    const isValidated = run.status === 'VALIDATED';
    const pillClass = isRejected ? 'rejected' : isValidated ? '' : 'pending';
    return `<button class="run-item ${run.id === selectId ? "active" : ""}" data-run="${run.id}">
      <svg class="run-item-icon" aria-hidden="true" width="18" height="18"><use href="#icon-document"/></svg>
      <div class="run-item-body">
        <span class="run-item-title" title="${esc(sourcePath)}">${esc(sourcePath)}</span>
        <span class="run-item-meta">
          <span>${esc(docType)}</span>
          <time>${stamp(run.completed_at)}</time>
        </span>
      </div>
      <span class="status-pill ${pillClass}">${esc(status)}</span>
    </button>`;
  }).join("");
  list.querySelectorAll("[data-run]").forEach(button => { button.onclick = () => openRun(button.dataset.run); });
}

async function loadRuns(selectId) {
  state.runs = await api("/api/runs");
  renderRuns(selectId);
  renderOverview();
}

function renderOverview() {
  const runs = state.runs || [];
  const byDate = (a, b) => new Date(b.completed_at || 0) - new Date(a.completed_at || 0);
  const recent = [...runs].sort(byDate).slice(0, 6);
  const extracted = runs.filter(run => run.status === "VALIDATED").sort(byDate).slice(0, 6);
  const rejected = runs.filter(run => run.latest_review === "REJECTED" || run.status === "FAILED").sort(byDate).slice(0, 6);

  function fill(cardId, items, emptyText) {
    const body = $(`#${cardId} .overview-body`);
    if (!body) return;
    if (!items.length) {
      body.innerHTML = `<p class="empty">${emptyText}</p>`;
      return;
    }
    body.innerHTML = "<ul>" + items.map(run => {
      const path = esc(run.source_relative_path || run.original_filename || "Unnamed");
      const time = stamp(run.completed_at);
      const status = esc(run.status.replaceAll("_", " "));
      return `<li title="${path}"><strong>${path}</strong><br><span class="overview-meta">${status} · ${time}</span></li>`;
    }).join("") + "</ul>";
  }

  fill("overview-recent", recent, "No documents yet.");
  fill("overview-extracted", extracted, "No extracted documents.");
  fill("overview-rejected", rejected, "No rejected documents.");
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
  if (render) {
    renderRuns(state.current?.id);
    ensureRunHistoryVisible();
  }
}

function ensureRunHistoryVisible() {
  const dashboardLower = $(".dashboard-lower");
  const runHistory = $("#run-history");
  const toggleButton = $("#toggle-run-history");
  if (dashboardLower) dashboardLower.classList.remove("run-history-collapsed");
  if (toggleButton) {
    toggleButton.setAttribute("aria-pressed", "false");
    toggleButton.setAttribute("aria-label", "Hide run history");
    toggleButton.title = "Hide run history";
  }
  if (runHistory) {
    const rect = runHistory.getBoundingClientRect();
    if (rect.top < 0 || rect.bottom > window.innerHeight) {
      runHistory.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }
}

function summary(data, warnings, errors) {
  const groups = [];
  const skip = new Set(["schema_version", "document_type", "classification_evidence", "transactions", "reconciliation", "source_extraction"]);
  const source = data.source_extraction;
  if (source) {
    groups.push(`<div class="summary-group"><h4>Page extraction</h4><div class="field-row"><span>Total PDF pages</span><strong>${source.total_pages}</strong></div><div class="field-row"><span>Pages with text</span><strong>${source.pages_with_text}</strong></div><div class="field-row"><span>Pages needing review</span><strong>${source.pages_needing_review}</strong></div>${source.page_results.map(page => `<div class="field-row page-row"><span>Page ${page.page_number}</span><div><strong class="method">${esc(page.method.replaceAll("_", " ").toUpperCase())}</strong><span class="char-count">${page.character_count.toLocaleString()} characters${page.needs_review ? " · REVIEW REQUIRED" : ""}</span></div></div>`).join("")}</div>`);
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

function changePdfPage(num) {
  if (!pdfState.pdf) return;
  if (num < 1) num = 1;
  if (num > pdfState.pageCount) num = pdfState.pageCount;
  pdfState.pageNum = num;
  updatePdfPageInfo();
  renderPdfPage(num);
}

function updatePdfPageInfo() {
  const pageNumInput = $("#pdf-page-num");
  const pageCountEl = $("#pdf-page-count");
  if (pageNumInput) pageNumInput.value = pdfState.pageNum;
  if (pageCountEl) pageCountEl.textContent = pdfState.pageCount;
}

function setPdfMode(mode) {
  if (!pdfState.pdf) return;
  pdfState.mode = mode;
  renderPdfPage(pdfState.pageNum);
}

function nudgePdfZoom(delta) {
  if (!pdfState.pdf) return;
  pdfState.mode = "manual";
  let newScale = (pdfState.scale || 1) * (1 + delta);
  if (newScale < 0.25) newScale = 0.25;
  if (newScale > 5) newScale = 5;
  pdfState.scale = newScale;
  renderPdfPage(pdfState.pageNum);
}

function updatePdfActiveMode() {
  const fitPage = $("#pdf-fit-page");
  const fitWidth = $("#pdf-fit-width");
  if (fitPage) fitPage.classList.toggle("active", pdfState.mode === "fit-page");
  if (fitWidth) fitWidth.classList.toggle("active", pdfState.mode === "fit-width");
}

function showPdfError(message) {
  const wrap = $("#pdf-viewer-wrap");
  if (!wrap) return;
  let errorEl = $("#pdf-error");
  if (!errorEl) {
    errorEl = document.createElement("p");
    errorEl.id = "pdf-error";
    errorEl.className = "pdf-error";
    wrap.appendChild(errorEl);
  }
  errorEl.textContent = message;
  const canvas = $("#pdf-viewer");
  if (canvas) canvas.style.visibility = "hidden";
}

function clearPdfError() {
  const errorEl = $("#pdf-error");
  if (errorEl) errorEl.remove();
  const canvas = $("#pdf-viewer");
  if (canvas) canvas.style.visibility = "visible";
}

async function renderPdfPage(num) {
  if (!pdfState.pdf) return;
  const canvas = $("#pdf-viewer");
  const wrap = $("#pdf-viewer-wrap");
  if (!canvas || !wrap) return;

  if (pdfState.renderTask) {
    try { pdfState.renderTask.cancel(); } catch {}
    pdfState.renderTask = null;
  }

  try {
    const page = await pdfState.pdf.getPage(num);
    const wrapRect = wrap.getBoundingClientRect();
    const viewport = page.getViewport({ scale: 1 });
    let scale = pdfState.scale;

    if (pdfState.mode === "fit-page") {
      scale = Math.min(wrapRect.width / viewport.width, wrapRect.height / viewport.height);
    } else if (pdfState.mode === "fit-width") {
      scale = wrapRect.width / viewport.width;
    }

    if (!scale || scale < 0.25) scale = 0.25;
    if (scale > 5) scale = 5;

    const scaledViewport = page.getViewport({ scale });
    const dpr = Math.max(window.devicePixelRatio || 1, 1);
    const renderWidth = Math.floor(scaledViewport.width * dpr);
    const renderHeight = Math.floor(scaledViewport.height * dpr);

    canvas.width = renderWidth;
    canvas.height = renderHeight;
    canvas.style.width = `${Math.floor(scaledViewport.width)}px`;
    canvas.style.height = `${Math.floor(scaledViewport.height)}px`;

    const ctx = canvas.getContext("2d", { alpha: false });
    ctx.fillStyle = "#fff";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    pdfState.renderTask = page.render({ canvasContext: ctx, viewport: scaledViewport });
    await pdfState.renderTask.promise;
    pdfState.scale = scale;
    pdfState.pageNum = num;
    updatePdfActiveMode();
    clearPdfError();
  } catch (error) {
    if (error.name !== "RenderingCancelledException") {
      console.error(error);
      showPdfError(error.message || "Could not render PDF page");
    }
  }
}

async function initPdfViewer(url) {
  const canvas = $("#pdf-viewer");
  const wrap = $("#pdf-viewer-wrap");
  if (!canvas || !wrap) return;

  if (typeof pdfjsLib === "undefined") {
    showPdfError("PDF viewer library not loaded");
    return;
  }

  pdfState.url = url;
  pdfState.pdf = null;
  pdfState.pageNum = 1;
  pdfState.pageCount = 1;
  pdfState.mode = "fit-page";
  pdfState.scale = 1;
  clearPdfError();

  try {
    const response = await fetch(url, { credentials: "include" });
    if (!response.ok) throw new Error(`PDF load failed: ${response.status}`);
    const data = await response.arrayBuffer();
    pdfState.pdf = await pdfjsLib.getDocument({ data }).promise;
    pdfState.pageCount = pdfState.pdf.numPages;
    pdfState.pageNum = 1;
    updatePdfPageInfo();
    wirePdfControls();
    await renderPdfPage(1);
  } catch (error) {
    console.error(error);
    showPdfError(error.message || "Failed to load PDF");
  }
}

function wirePdfControls() {
  $("#pdf-prev").onclick = () => changePdfPage(pdfState.pageNum - 1);
  $("#pdf-next").onclick = () => changePdfPage(pdfState.pageNum + 1);
  $("#pdf-page-num").onchange = e => changePdfPage(parseInt(e.target.value, 10) || 1);
  $("#pdf-zoom-out").onclick = () => nudgePdfZoom(-0.15);
  $("#pdf-zoom-in").onclick = () => nudgePdfZoom(0.15);
  $("#pdf-fit-page").onclick = () => setPdfMode("fit-page");
  $("#pdf-fit-width").onclick = () => setPdfMode("fit-width");
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

  const statusEl = $("#run-status");
  if (statusEl) {
    statusEl.textContent = (run.status || "PENDING").replaceAll("_", " ");
    statusEl.className = `run-status ${run.status === "VALIDATED" ? "good" : run.status === "FAILED" ? "bad" : ""}`;
  }

  $("#metric-type").textContent = run.document_type.replaceAll("_", " ");
  $("#metric-classification").textContent = pct(run.classification_confidence);
  $("#metric-extraction").textContent = pct(run.extraction_confidence);
  $("#metric-ocr").textContent = run.ocr_used ? "Used" : "Not needed";

  const pdfUrl = `/api/documents/${run.document_id}/file`;
  $("#open-pdf").href = pdfUrl;
  $("#export").href = `/api/runs/${run.id}/json`;

  const summaryHtml = summary(run.normalized, run.warnings, run.errors);
  $("#tab-compare").innerHTML = summaryHtml;
  $("#tab-structured").innerHTML = summaryHtml;
  $("#json-output").textContent = JSON.stringify(run.normalized, null, 2);
  $("#raw-output").textContent = run.raw_text || "No text extracted.";
  $("#provenance-body").innerHTML = run.fields.map(field => `<tr><td>${esc(field.field_path)}</td><td>${esc(typeof field.value === "object" ? JSON.stringify(field.value) : field.value)}</td><td>${esc(field.page_number)}</td><td>${pct(field.confidence)}</td><td>${esc(field.source_text)}</td></tr>`).join("") || '<tr><td colspan="5">No field-level provenance was created.</td></tr>';
  $("#review-history").innerHTML = run.reviews.map(review => `<div class="review-record"><strong>${esc(review.decision.replaceAll("_", " "))}</strong><span>${stamp(review.reviewed_at)} · ${esc(review.note || "No note")}</span></div>`).join("");

  document.querySelectorAll(".analysis-tabs button").forEach(button => {
    button.onclick = () => {
      document.querySelectorAll(".analysis-tabs button").forEach(b => b.classList.remove("active"));
      document.querySelectorAll(".analysis-body .tab-pane").forEach(el => el.classList.remove("active"));
      button.classList.add("active");
      const tabEl = $(`#tab-${button.dataset.tab}`);
      if (tabEl) tabEl.classList.add("active");
      const interpretation = $("#interpretation-panel");
      if (interpretation) interpretation.hidden = true;
    };
  });

  $("#rerun").onclick = rerun;
  document.querySelectorAll("[data-decision]").forEach(button => { button.onclick = () => review(button.dataset.decision); });

  initPdfViewer(pdfUrl);
  renderRuns(id);
  ensureRunHistoryVisible();
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

function setSidebarActive(target) {
  document.querySelectorAll(".nav-item").forEach(item => {
    const active = item.dataset.target === target;
    item.classList.toggle("active", active);
    if (active) item.setAttribute("aria-current", "page");
    else item.removeAttribute("aria-current");
  });
}

document.querySelectorAll(".nav-item").forEach(item => {
  item.onclick = event => {
    if (item.classList.contains("disabled")) {
      event.preventDefault();
      return;
    }
    const target = item.dataset.target;
    if (!target) return;
    if (target === "support") {
      if (typeof BRANDING === "undefined" || !BRANDING.supportEmail) event.preventDefault();
      return;
    }
    event.preventDefault();
    setSidebarActive(target);
    const section = $(`#${target}`);
    if (section) section.scrollIntoView({behavior: "smooth", block: "start"});
    else if (target === "dashboard") window.scrollTo({top: 0, behavior: "smooth"});
  };
});

const runHistory = $("#run-history");
const dashboardLower = $(".dashboard-lower");
const toggleRunHistory = $("#toggle-run-history");
if (toggleRunHistory && dashboardLower) {
  toggleRunHistory.onclick = () => {
    const collapsed = dashboardLower.classList.toggle("run-history-collapsed");
    toggleRunHistory.setAttribute("aria-pressed", String(collapsed));
    toggleRunHistory.setAttribute("aria-label", collapsed ? "Show run history" : "Hide run history");
    toggleRunHistory.title = collapsed ? "Show run history" : "Hide run history";
    toggleRunHistory.classList.toggle("is-collapsed", collapsed);
  };
}

$("#refresh").onclick = () => { if (isAuthenticated) { loadRuns(state.current?.id); loadDashboard(); } };
$("#login-form").onsubmit = login;
$("#logout-button").onclick = logout;

const passwordInput = $("#login-password");
const passwordToggle = $("#login-toggle-password");
const eyeOpen = $("#eye-open");
const eyeClosed = $("#eye-closed");

if (passwordInput && passwordToggle) {
  passwordToggle.addEventListener("click", () => {
    const isPassword = passwordInput.type === "password";
    passwordInput.type = isPassword ? "text" : "password";
    passwordToggle.setAttribute("aria-label", isPassword ? "Hide password" : "Show password");
    passwordToggle.setAttribute("aria-pressed", String(isPassword));
    if (eyeOpen && eyeClosed) {
      eyeOpen.hidden = isPassword;
      eyeClosed.hidden = !isPassword;
    }
  });
}

checkHealth();
handleAuthHash().then(() => checkAuth()).catch(() => checkAuth());
