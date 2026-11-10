/* Dashboard: KPIs + charts from GET /api/metrics/summary (no hard-coded numbers). */
"use strict";

function kpi(label, value, sub = "", cls = "") {
  return `<div class="card kpi ${cls}"><div class="label">${esc(label)}</div><div class="value">${value}</div><div class="sub">${sub}</div></div>`;
}

function runsChart(runs) {
  const data = runs.slice().reverse();
  if (!data.length) return `<div class="empty">No runs yet.</div>`;
  const W = 560, H = 210, pad = 30, bw = Math.min(38, (W - pad) / data.length - 8);
  const max = Math.max(1, ...data.map(r => r.input_count));
  const bars = data.map((r, i) => {
    const x = pad + i * ((W - pad) / data.length) + 4;
    const scale = v => (v / max) * (H - 40);
    const vh = scale(r.valid_count), rh = scale(r.rejected_count);
    const blocked = r.status === "FAILED" || r.status === "SKIPPED_DUPLICATE";
    const gh = blocked ? Math.max(scale(r.input_count), 3) : 0;
    const tip = `${r.run_id}\n${r.dataset_name} · ${r.status}\ninput ${r.input_count}, valid ${r.valid_count}, rejected ${r.rejected_count}`;
    return `<g><title>${esc(tip)}</title>
      ${blocked ? `<rect x="${x}" y="${H - 20 - gh}" width="${bw}" height="${gh}" fill="#c3c9d6" rx="2"/>` :
      `<rect x="${x}" y="${H - 20 - vh}" width="${bw}" height="${vh}" fill="var(--ok)" rx="2"/>
       <rect x="${x}" y="${H - 20 - vh - rh}" width="${bw}" height="${rh}" fill="var(--bad)" rx="2"/>`}
      <text x="${x + bw / 2}" y="${H - 6}" font-size="10" text-anchor="middle" fill="var(--muted)">${i + 1}</text></g>`;
  }).join("");
  return `<svg viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="Valid and rejected records per run">
    <line x1="${pad}" y1="${H - 20}" x2="${W}" y2="${H - 20}" stroke="var(--border)"/>
    <text x="0" y="14" font-size="10" fill="var(--muted)">${fmtNum(max)}</text>${bars}</svg>`;
}

function barList(items, colorVar = "var(--bad)") {
  if (!items.length) return `<div class="empty">Nothing to show yet.</div>`;
  const max = Math.max(...items.map(i => i.count));
  return items.map(i => `<div class="bar-row" title="${esc(i.title || "")}"><span class="mono" style="overflow:hidden;text-overflow:ellipsis">${esc(i.label)}</span>
    <div class="bar-track"><div class="bar-fill" style="width:${(i.count / max) * 100}%;background:${i.color || colorVar}"></div></div>
    <span class="right">${fmtNum(i.count)}</span></div>`).join("");
}

function dailyTable(days) {
  if (!days.length) return `<div class="empty">No runs in this selection.</div>`;
  return `<div class="table-wrap"><table><thead><tr><th>Date</th><th class="num">Runs</th><th class="num">Failed runs</th>
    <th class="num">Records</th><th class="num">Valid</th><th class="num">Rejected</th><th class="num">Success</th></tr></thead><tbody>
    ${days.slice().reverse().map(d => `<tr><td>${esc(d.date)}</td><td class="num">${fmtNum(d.runs)}</td>
      <td class="num">${d.failed_runs ? `<span class="badge b-bad">${fmtNum(d.failed_runs)}</span>` : "0"}</td>
      <td class="num">${fmtNum(d.processed)}</td><td class="num">${fmtNum(d.valid)}</td><td class="num">${fmtNum(d.rejected)}</td>
      <td class="num">${fmtPct(d.success_pct)}</td></tr>`).join("")}</tbody></table></div>`;
}

function filterQuery() {
  const q = new URLSearchParams();
  if ($("#f-contract").value) q.set("contract_id", $("#f-contract").value);
  if ($("#f-from").value) q.set("date_from", $("#f-from").value);
  if ($("#f-to").value) q.set("date_to", $("#f-to").value);
  return q;
}

async function loadDashboard() {
  const err = $("#error");
  err.innerHTML = "";
  const q = filterQuery();
  history.replaceState(null, "", q.toString() ? "?" + q : location.pathname);
  const parts = [];
  if ($("#f-contract").value) parts.push($("#f-contract").selectedOptions[0].textContent.split(" ·")[0]);
  if ($("#f-from").value || $("#f-to").value) parts.push(`${$("#f-from").value || "…"} → ${$("#f-to").value || "…"}`);
  $("#filter-label").textContent = parts.length ? "Showing: " + parts.join(" · ") : "Showing: all contracts, all dates";
  try {
    const m = await api("/metrics/summary" + (q.toString() ? "?" + q : ""));
    $("#daily").innerHTML = dailyTable(m.daily);
    $("#kpis").innerHTML = [
      kpi("Total runs", fmtNum(m.total_runs), `${fmtNum(m.contracts)} contracts`),
      kpi("Records processed", fmtNum(m.total_records_processed), `avg run ${fmtMs(m.avg_duration_ms && Math.round(m.avg_duration_ms))}`),
      kpi("Valid records", fmtNum(m.valid_records), `${fmtNum(m.published_rows_current)} rows in target`, "ok"),
      kpi("Rejected records", fmtNum(m.rejected_records), "in quarantine", m.rejected_records ? "bad" : ""),
      kpi("Validation success", fmtPct(m.validation_success_pct), "valid / (valid + rejected)", "ok"),
      kpi("Breaking changes", fmtNum(m.breaking_changes), "batch + contract", m.breaking_changes ? "bad" : ""),
      kpi("Non-breaking changes", fmtNum(m.non_breaking_changes), "batch + contract", "warn"),
      kpi("Failed runs", fmtNum(m.failed_runs), `${fmtNum(m.runs_by_status.SKIPPED_DUPLICATE || 0)} duplicates skipped`, m.failed_runs ? "bad" : ""),
    ].join("");
    $("#runs-chart").innerHTML = runsChart(m.recent_runs);
    $("#errors-chart").innerHTML = barList(m.top_error_codes.map(e => ({ label: e.error_code, count: e.count, title: e.description })));
    const statusColor = { COMPLETED: "var(--ok)", COMPLETED_WITH_ERRORS: "var(--warn)", FAILED: "var(--bad)" };
    $("#status-chart").innerHTML = barList(Object.entries(m.runs_by_status).map(([s, n]) =>
      ({ label: s, count: n, color: statusColor[s] || "#9aa3b5" })));
    $("#changes-chart").innerHTML = barList([
      { label: "BREAKING", count: m.breaking_changes, color: "var(--bad)" },
      { label: "NON_BREAKING", count: m.non_breaking_changes, color: "var(--ok)" },
    ].filter(x => x.count)) ;
    $("#recent").innerHTML = runsTable(m.recent_runs, { compact: true });
    $("#updated").textContent = "Updated " + new Date().toLocaleTimeString();
  } catch (e) { showError(err, e); }
}

function isoDay(d) { return d.toISOString().slice(0, 10); }

document.addEventListener("DOMContentLoaded", async () => {
  const p = params();
  try { await fillContractSelect($("#f-contract"), { allowAll: true }); } catch (e) { /* shown by loadDashboard */ }
  if (p.get("contract_id")) $("#f-contract").value = p.get("contract_id");
  if (p.get("date_from")) $("#f-from").value = p.get("date_from");
  if (p.get("date_to")) $("#f-to").value = p.get("date_to");
  ["#f-contract", "#f-from", "#f-to"].forEach(s => $(s).addEventListener("change", loadDashboard));
  document.querySelectorAll("[data-range]").forEach(b => b.addEventListener("click", () => {
    const today = new Date();
    const from = new Date(today.getTime() - (+b.dataset.range) * 86400000);
    $("#f-from").value = isoDay(from);
    $("#f-to").value = isoDay(today);
    loadDashboard();
  }));
  $("#f-reset").addEventListener("click", () => {
    $("#f-contract").value = ""; $("#f-from").value = ""; $("#f-to").value = "";
    loadDashboard();
  });
  loadDashboard();
  $("#refresh").addEventListener("click", loadDashboard);
  setInterval(loadDashboard, 15000);
});
