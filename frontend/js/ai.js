/* AI assistant page: advisory explanations next to the authoritative facts. */
"use strict";

let contractList = [];

function syncFields() {
  const contractTask = $("#task").value === "explain_contract_version";
  $("#run-field").hidden = contractTask;
  $("#contract-field").hidden = !contractTask;
}

function fillVersions() {
  const c = contractList.find(x => x.id === +$("#contract").value);
  $("#version").innerHTML = c ? c.versions.slice().reverse().map(n => `<option value="${n}">v${n}</option>`).join("") : "";
}

function renderAnswer(res) {
  const i = res.insight;
  const list = arr => arr.length ? `<ul>${arr.map(x => `<li>${esc(x)}</li>`).join("")}</ul>` : `<p class="muted">–</p>`;
  const facts = res.facts.run
    ? `Status ${badge(res.facts.run.status)} · input ${fmtNum(res.facts.run.input_count)} · valid ${fmtNum(res.facts.run.valid_count)} · rejected ${fmtNum(res.facts.run.rejected_count)} · breaking changes ${fmtNum(res.facts.run.breaking_change_count)}`
    : `Contract version v${esc(res.facts.version)} · ${fmtNum(res.facts.changes.length)} change(s), ${fmtNum(res.facts.breaking_count)} breaking`;
  $("#output").innerHTML = `
    <h2><span>Explanation</span><span>${badge(res.risk_level)} <span class="badge ${res.provider === "llm" ? "b-info" : "b-muted"}">${res.provider === "llm" ? "LLM: " + esc(res.model) : "deterministic (mock)"}</span></span></h2>
    <div class="alert info"><strong>Authoritative facts (rule engine):</strong> ${facts}</div>
    ${res.fallback_reason ? `<div class="alert warn small">${esc(res.fallback_reason)}</div>` : ""}
    <p>${esc(i.summary)}</p>
    <h3>Key findings</h3>${list(i.key_findings)}
    <h3>Recommended actions</h3>${list(i.recommended_actions)}
    <p class="small muted" style="margin-top:14px">${esc(res.disclaimer)} · ${fmtMs(res.latency_ms)}</p>
    <details><summary class="small muted">Facts sent to the model (JSON)</summary><pre class="json">${esc(JSON.stringify(res.facts, null, 2))}</pre></details>`;
}

async function ask(e) {
  e.preventDefault();
  const task = $("#task").value;
  const body = { task };
  if (task === "explain_contract_version") { body.contract_id = +$("#contract").value; body.version = +$("#version").value; }
  else {
    body.run_id = $("#run").value;
    if (!body.run_id) { toast("Select a run first", true); return; }
  }
  $("#ask").disabled = true;
  $("#output").innerHTML = `<div class="empty"><span class="spinner"></span> Generating… (local LLMs can take up to a minute)</div>`;
  try { renderAnswer(await api("/ai/explain", { method: "POST", body })); }
  catch (err) { showError($("#output"), err); }
  finally { $("#ask").disabled = false; }
}

document.addEventListener("DOMContentLoaded", async () => {
  try {
    const s = await api("/ai/status");
    $("#ai-status").innerHTML = s.configured
      ? `Provider: <strong>${esc(s.model)}</strong> (${s.reachable ? "reachable" : "unreachable - mock fallback will be used"})`
      : "Provider: <strong>mock</strong> (set LLM_PROVIDER / LLM_BASE_URL / LLM_MODEL in .env for a real LLM)";
    await fillRunSelect($("#run"), params().get("run_id"));
    contractList = await fillContractSelect($("#contract"));
    fillVersions();
  } catch (err) { showError($("#output"), err); }
  $("#task").addEventListener("change", syncFields);
  $("#contract").addEventListener("change", fillVersions);
  $("#ai-form").addEventListener("submit", ask);
  syncFields();
});
