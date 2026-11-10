/* Pipeline run history with failure details and retry. */
"use strict";

const PAGE = 50;
let offset = 0;

async function load() {
  const q = new URLSearchParams({ limit: PAGE, offset });
  if ($("#f-status").value) q.set("status", $("#f-status").value);
  if ($("#f-contract").value) q.set("contract_id", $("#f-contract").value);
  const box = $("#table");
  let res;
  try { res = await api(`/runs?${q}`); } catch (e) { return showError(box, e); }
  if (!res.items.length) { box.innerHTML = `<div class="empty">No runs match.</div>`; return; }
  box.innerHTML = `<div class="table-wrap"><table><thead><tr>
      <th>Run ID</th><th>Dataset</th><th>Source file</th><th>Status</th><th class="num">Input</th><th class="num">Valid</th>
      <th class="num">Rejected</th><th class="num">Published</th><th class="num">Schema Δ (breaking)</th><th class="num">Duration</th>
      <th>Started</th><th>Failure details</th><th></th></tr></thead><tbody>
    ${res.items.map(r => `<tr>
      <td>${runLink(r.run_id)}${r.attempt > 1 ? `<div class="small muted">attempt ${r.attempt}</div>` : ""}</td>
      <td>${esc(r.dataset_name)} <span class="muted">v${esc(r.contract_version)}</span></td>
      <td class="mono small">${esc(r.source_file_name)}<div class="muted">${esc(r.source_system || "")}</div></td>
      <td>${badge(r.status)}</td>
      <td class="num">${fmtNum(r.input_count)}</td><td class="num">${fmtNum(r.valid_count)}</td><td class="num">${fmtNum(r.rejected_count)}</td>
      <td class="num">${fmtNum(r.published_count)}${r.unchanged_count ? `<div class="small muted">${fmtNum(r.unchanged_count)} unchanged</div>` : ""}</td>
      <td class="num">${r.schema_change_count} (${r.breaking_change_count})</td>
      <td class="num">${fmtMs(r.duration_ms)}</td><td class="small">${fmtDate(r.started_at || r.created_at)}</td>
      <td class="small" style="max-width:340px">${esc(r.failure_reason || "")}</td>
      <td>${r.status === "FAILED" ? `<button class="btn small" data-retry="${esc(r.run_id)}">Retry</button>` : ""}</td></tr>`).join("")}
    </tbody></table></div>
    <div class="pager"><span class="muted small">${fmtNum(offset + 1)}–${fmtNum(offset + res.items.length)} of ${fmtNum(res.total)}</span>
      <span><button class="btn secondary small" id="prev" ${offset ? "" : "disabled"}>Prev</button>
      <button class="btn secondary small" id="next" ${offset + PAGE < res.total ? "" : "disabled"}>Next</button></span></div>`;
  box.querySelectorAll("[data-retry]").forEach(b => b.addEventListener("click", async () => {
    b.disabled = true;
    try {
      const nr = await api(`/runs/${encodeURIComponent(b.dataset.retry)}/retry`, { method: "POST" });
      toast(`Retry started as ${nr.run_id}`);
      setTimeout(load, 1500);
    } catch (e) { toast(e.message, true); b.disabled = false; }
  }));
  $("#prev")?.addEventListener("click", () => { offset = Math.max(0, offset - PAGE); load(); });
  $("#next")?.addEventListener("click", () => { offset += PAGE; load(); });
}

document.addEventListener("DOMContentLoaded", async () => {
  try { await fillContractSelect($("#f-contract"), { allowAll: true }); } catch (e) { /* table shows the error */ }
  ["#f-status", "#f-contract"].forEach(s => $(s).addEventListener("change", () => { offset = 0; load(); }));
  $("#refresh").addEventListener("click", load);
  load();
});
