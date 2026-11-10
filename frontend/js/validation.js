/* Validation report for one run. runSummaryHtml() is shared with the ingestion page. */
"use strict";

function runSummaryHtml(run) {
  const t = run.transformation_summary || {};
  const stages = t.stage_timings_ms || {};
  const kv = (obj) => Object.entries(obj || {}).map(([k, v]) => `<span class="chip">${esc(k)}: ${esc(typeof v === "object" ? JSON.stringify(v) : v)}</span>`).join("") || "<span class='muted'>none</span>";
  return `
    ${run.failure_reason ? `<div class="alert ${run.status === "SKIPPED_DUPLICATE" ? "info" : "bad"}">${esc(run.failure_reason)}</div>` : ""}
    <div class="grid kpis" style="grid-template-columns:repeat(auto-fill,minmax(120px,1fr))">
      ${[["Input", run.input_count], ["Valid", run.valid_count, "ok"], ["Rejected", run.rejected_count, run.rejected_count ? "bad" : ""],
         ["Published", run.published_count, "ok"], ["Unchanged", run.unchanged_count], ["Stale skipped", run.stale_skipped_count],
         ["Late arriving", run.late_arriving_count], ["Success", fmtPct(run.success_rate)]]
        .map(([l, v, c]) => `<div class="card kpi ${c || ""}" style="padding:10px 12px"><div class="label">${l}</div><div class="value" style="font-size:20px">${typeof v === "number" ? fmtNum(v) : v}</div></div>`).join("")}
    </div>
    <table class="small"><tbody>
      <tr><td class="muted">Contract</td><td>${esc(run.contract_name)} <span class="mono">${esc(run.dataset_name)}</span> v${esc(run.contract_version)}</td></tr>
      <tr><td class="muted">Source</td><td class="mono">${esc(run.source_file_name)} · ${esc(run.source_system || "unknown system")}</td></tr>
      <tr><td class="muted">Checksum (SHA-256)</td><td class="mono">${esc(run.source_checksum)}</td></tr>
      <tr><td class="muted">Timing</td><td>${fmtDate(run.started_at)} → ${fmtDate(run.ended_at)} · <strong>${fmtMs(run.duration_ms)}</strong> · attempt ${run.attempt}${run.force_reprocess ? " · forced" : ""}</td></tr>
      ${run.duplicate_of_run_id ? `<tr><td class="muted">Duplicate of</td><td>${runLink(run.duplicate_of_run_id)}</td></tr>` : ""}
      <tr><td class="muted">Stage timings</td><td>${kv(stages)}</td></tr>
      <tr><td class="muted">Transformations</td><td>${kv(Object.fromEntries(Object.entries(t).filter(([k, v]) => k !== "stage_timings_ms" && v && Object.keys(v).length)))}</td></tr>
    </tbody></table>`;
}

function errorBars(summary) {
  if (!summary.length) return `<div class="empty">No record-level errors.</div>`;
  const max = Math.max(...summary.map(e => e.count));
  return summary.map(e => `<div class="bar-row" title="${esc(e.description || "")}">
      <span><a class="mono" href="rejected.html?run_id=${encodeURIComponent(currentRunId)}&error_code=${encodeURIComponent(e.error_code)}">${esc(e.error_code)}</a>
      <span class="muted small">${esc(e.column_name || "")}</span></span>
      <div class="bar-track"><div class="bar-fill" style="width:${(e.count / max) * 100}%"></div></div><span class="right">${fmtNum(e.count)}</span></div>`).join("");
}

let currentRunId = null;

async function loadReport(runId) {
  currentRunId = runId;
  const box = $("#report");
  if (!runId) { box.innerHTML = `<div class="empty">Select a run.</div>`; return; }
  box.innerHTML = `<div class="empty"><span class="spinner"></span></div>`;
  let rep;
  try { rep = await api(`/runs/${encodeURIComponent(runId)}/report`); } catch (e) { return showError(box, e); }
  history.replaceState(null, "", `?run_id=${encodeURIComponent(runId)}`);
  const run = rep.run;
  const breaking = rep.schema_changes.filter(c => c.classification === "BREAKING").length;
  box.innerHTML = `
    <div class="card"><h2><span>Run ${esc(run.run_id)} ${badge(run.status)}</span>
      <span class="row shrink">
        <a class="btn secondary small shrink" href="ai.html?run_id=${encodeURIComponent(run.run_id)}">✦ Explain with AI</a>
        ${run.status === "FAILED" ? `<button class="btn small shrink" id="retry">Retry run</button>` : ""}</span></h2>
      ${runSummaryHtml(run)}</div>
    <div class="card" style="margin-top:16px"><h2>Schema validation report
      <span>${breaking ? `<span class="badge b-bad">${breaking} breaking</span>` : ""} <span class="badge b-ok">${rep.schema_changes.length - breaking} non-breaking</span></span></h2>
      ${changesTable(rep.schema_changes)}</div>
    <div class="grid two" style="margin-top:16px">
      <div class="card"><h2>Record-level errors <span class="small muted">a record can have several</span></h2>${errorBars(rep.error_summary)}</div>
      <div class="card"><h2>Error explanations</h2>${rep.error_summary.length ? `<table class="small"><tbody>
        ${[...new Map(rep.error_summary.map(e => [e.error_code, e])).values()].map(e => `<tr><td>${badge(e.error_code)}</td><td>${esc(e.description || "")}</td></tr>`).join("")}</tbody></table>` : `<div class="empty">–</div>`}</div>
    </div>
    <div class="card" style="margin-top:16px"><h2>Sample rejected records
      ${run.rejected_count ? `<a class="small" href="rejected.html?run_id=${encodeURIComponent(run.run_id)}">All ${fmtNum(run.rejected_count)} →</a>` : ""}</h2>
      ${rep.sample_rejections.length ? `<div class="table-wrap"><table><thead><tr><th class="num">Row</th><th>Error codes</th><th>Reasons</th></tr></thead><tbody>
        ${rep.sample_rejections.map((r, i) => `<tr class="clickable" data-i="${i}"><td class="num">${r.source_row_number}</td><td>${chips(r.error_codes)}</td>
          <td class="small">${r.errors.map(e => esc(e.message)).join("<br>")}</td></tr>`).join("")}</tbody></table></div>`
        : `<div class="empty">${run.status === "FAILED" && breaking ? "Records were not validated because a breaking schema change blocked the batch." : "No rejected records."}</div>`}
    </div>`;
  box.querySelectorAll("tr.clickable").forEach(tr => tr.addEventListener("click", () =>
    modal(`Rejected row ${rep.sample_rejections[+tr.dataset.i].source_row_number}`, rejectionDetailHtml(rep.sample_rejections[+tr.dataset.i]))));
  $("#retry")?.addEventListener("click", async () => {
    try {
      const nr = await api(`/runs/${encodeURIComponent(run.run_id)}/retry`, { method: "POST" });
      toast(`Retry started: ${nr.run_id}`);
      setTimeout(async () => { await fillRunSelect($("#run-select"), nr.run_id); loadReport(nr.run_id); }, 1500);
    } catch (e) { toast(e.message, true); }
  });
}

document.addEventListener("DOMContentLoaded", async () => {
  if (!$("#report")) return;          // file is also loaded by ingest.html for runSummaryHtml()
  const pre = params().get("run_id");
  try {
    const runs = await fillRunSelect($("#run-select"), pre);
    loadReport(pre || (runs[0] && runs[0].run_id));
    if (!pre && runs[0]) $("#run-select").value = runs[0].run_id;
  } catch (e) { showError($("#report"), e); }
  $("#run-select").addEventListener("change", e => loadReport(e.target.value));
});
