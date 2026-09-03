(() => {
  const escapeHtml = value => String(value ?? "—").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const currentRunId = () => document.querySelector("#export")?.getAttribute("href")?.match(/\/api\/runs\/([^/]+)\/json/)?.[1];

  function renderInterpretation(item) {
    const panel = document.querySelector("#interpretation-panel");
    if (!panel || !item) return;
    const result = item.result;
    const recon = result.reconciliation;
    panel.hidden = false;
    panel.innerHTML = `
      <div class="panel-title"><h3>Step 2 · Bank Statement Interpretation</h3><a href="/api/interpretations/${item.id}/json">Export interpretation</a></div>
      <div class="interpretation-grid">
        <article><span>Institution</span><strong>${escapeHtml(result.institution.name)}</strong></article>
        <article><span>Account</span><strong>${escapeHtml(result.account.account_type)} · ${escapeHtml(result.account.last4)}</strong></article>
        <article><span>Reconciliation</span><strong class="recon-${recon.status.toLowerCase()}">${escapeHtml(recon.status)}</strong></article>
        <article><span>Transactions</span><strong>${result.transactions.length} · ${recon.unresolved_transaction_count} unresolved</strong></article>
        <article><span>Printed credits</span><strong>$${escapeHtml(recon.printed_total_credits)}</strong></article>
        <article><span>Interpreted credits</span><strong>$${escapeHtml(recon.interpreted_total_credits)}</strong></article>
        <article><span>Printed debits</span><strong>$${escapeHtml(recon.printed_total_debits)}</strong></article>
        <article><span>Interpreted debits</span><strong>$${escapeHtml(recon.interpreted_total_debits)}</strong></article>
      </div>
      ${result.warnings.length ? `<div class="warning-list">${result.warnings.map(w => `<p>${escapeHtml(w)}</p>`).join("")}</div>` : ""}
      <div class="table-wrap"><table><thead><tr><th>Date</th><th>Description</th><th>Amount</th><th>Direction</th><th>Evidence</th><th>Page</th></tr></thead>
      <tbody>${result.transactions.map(tx => `<tr><td>${escapeHtml(tx.date)}</td><td>${escapeHtml(tx.description)}</td><td>$${escapeHtml(tx.amount)}</td><td>${escapeHtml(tx.direction)}</td><td>${escapeHtml(tx.direction_source)} · ${escapeHtml(tx.direction_confidence)}</td><td>${escapeHtml(tx.page_number)}</td></tr>`).join("") || '<tr><td colspan="6">No transaction rows were interpreted.</td></tr>'}</tbody></table></div>`;
  }

  async function loadLatest(runId) {
    const response = await fetch(`/api/runs/${runId}/interpretations`);
    if (response.ok) {
      const items = await response.json();
      const panel = document.querySelector("#interpretation-panel");
      if (items.length) renderInterpretation(items[0]); else if (panel) panel.hidden = true;
    }
  }

  document.addEventListener("click", async event => {
    if (event.target?.id !== "interpret") return;
    const runId = currentRunId();
    if (!runId) return;
    const button = event.target;
    button.disabled = true;
    button.textContent = "Interpreting…";
    try {
      const response = await fetch(`/api/runs/${runId}/interpretations/bank-statement`, {method: "POST"});
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.detail || "Interpretation failed");
      renderInterpretation(payload);
    } catch (error) {
      alert(error.message);
    } finally {
      button.disabled = false;
      button.textContent = "Interpret Bank Statement";
    }
  });

  new MutationObserver(() => {
    const runId = currentRunId();
    const coverage = document.querySelector("#metric-classification");
    const transcription = document.querySelector("#metric-extraction");
    if (coverage && transcription) coverage.textContent = transcription.textContent;
    if (runId && document.querySelector("#interpretation-panel")) loadLatest(runId);
  }).observe(document.querySelector("#review-panel"), {childList: true});
})();
