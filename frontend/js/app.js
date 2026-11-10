/* Shared helpers: API access (Fetch), sidebar, escaping, formatting, small UI widgets.
   SECURITY: anything coming from the API (especially producer data in rejected records)
   is rendered through esc() or textContent - never inserted as raw HTML. */
"use strict";

const API = "/api";

async function api(path, options = {}) {
  const opts = { ...options, headers: { ...(options.headers || {}) } };
  if (opts.body && !(opts.body instanceof FormData) && typeof opts.body !== "string") {
    opts.body = JSON.stringify(opts.body);
    opts.headers["Content-Type"] = "application/json";
  }
  let resp;
  try {
    resp = await fetch(API + path, opts);
  } catch (e) {
    throw new ApiError("Cannot reach the backend. Is the server running?", 0, null);
  }
  const isJson = (resp.headers.get("content-type") || "").includes("application/json");
  const body = isJson ? await resp.json() : await resp.text();
  if (!resp.ok) {
    let msg = (body && body.detail) || resp.statusText;
    if (body && body.errors) msg += ": " + body.errors.map(e => `${e.loc.slice(1).join(".")} - ${e.msg}`).join("; ");
    throw new ApiError(msg, resp.status, body);
  }
  return body;
}

class ApiError extends Error {
  constructor(message, status, body) { super(message); this.status = status; this.body = body; }
}

function esc(value) {
  if (value === null || value === undefined) return "";
  return String(value).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

const $ = (sel, root = document) => root.querySelector(sel);
const fmtNum = n => (n === null || n === undefined) ? "–" : Number(n).toLocaleString();
const fmtPct = n => (n === null || n === undefined) ? "–" : `${Number(n).toFixed(1)}%`;
function fmtMs(ms) {
  if (ms === null || ms === undefined) return "–";
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(2)} s`;
}
function fmtDate(iso) {
  if (!iso) return "–";
  const d = new Date(iso.endsWith("Z") ? iso : iso + "Z");   // API timestamps are UTC
  return d.toLocaleString();
}

const STATUS_CLASS = {
  COMPLETED: "b-ok", COMPLETED_WITH_ERRORS: "b-warn", FAILED: "b-bad", RUNNING: "b-info",
  PENDING: "b-muted", SKIPPED_DUPLICATE: "b-muted", BREAKING: "b-bad", NON_BREAKING: "b-ok",
  CRITICAL: "b-bad", WARNING: "b-warn", INFO: "b-info", HIGH: "b-bad", MEDIUM: "b-warn", LOW: "b-ok",
};
const badge = (text) => `<span class="badge ${STATUS_CLASS[text] || "b-muted"}">${esc(text)}</span>`;
const chips = (list) => (list || []).map(c => `<span class="chip">${esc(c)}</span>`).join("");
const runLink = (id) => id ? `<a href="validation.html?run_id=${encodeURIComponent(id)}" class="mono">${esc(id)}</a>` : "–";

function toast(message, bad = false) {
  const el = document.createElement("div");
  el.className = "toast" + (bad ? " bad" : "");
  el.textContent = message;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), bad ? 6000 : 3000);
}

function showError(container, err) {
  container.innerHTML = `<div class="alert bad">${esc(err.message || err)}</div>`;
}

function modal(title, html) {
  const back = document.createElement("div");
  back.className = "modal-back";
  back.innerHTML = `<div class="modal" role="dialog" aria-modal="true">
      <div class="page-head"><h1 style="font-size:18px">${esc(title)}</h1>
      <button class="btn secondary small" data-close>Close</button></div>${html}</div>`;
  back.addEventListener("click", e => { if (e.target === back || e.target.hasAttribute("data-close")) back.remove(); });
  document.addEventListener("keydown", function onKey(e) { if (e.key === "Escape") { back.remove(); document.removeEventListener("keydown", onKey); } });
  document.body.appendChild(back);
  return back;
}

const params = () => new URLSearchParams(location.search);

/* ---------------- sidebar ---------------- */
const NAV = [
  ["index.html", "▦", "Dashboard"], ["contracts.html", "▤", "Contracts"], ["ingest.html", "⇪", "Data Ingestion"],
  ["validation.html", "✓", "Validation Results"], ["rejected.html", "⚠", "Rejected Records"],
  ["runs.html", "↻", "Pipeline Runs"], ["ai.html", "✦", "AI Assistant"],
];

function renderSidebar() {
  const side = $("#sidebar");
  if (!side) return;
  const page = location.pathname.split("/").pop() || "index.html";
  side.className = "sidebar";
  side.innerHTML = `
    <div class="brand">Data Contract Platform<small>Validation · Quarantine · Publishing</small></div>
    <nav class="nav">${NAV.map(([href, ico, label]) =>
      `<a href="${href}" class="${href === page ? "active" : ""}"><span class="ico">${ico}</span>${label}</a>`).join("")}</nav>
    <div class="side-foot">
      <div><span class="dot" id="db-dot"></span><span id="db-status">checking database…</span></div>
      <div style="margin-top:6px"><span class="dot" id="llm-dot"></span><span id="llm-status">checking AI…</span></div>
      <div style="margin-top:10px"><a href="/docs" target="_blank" style="color:#b9c2d6">API docs ↗</a></div>
    </div>`;
  api("/health").then(h => {
    $("#db-dot").className = "dot " + (h.database.status === "up" ? "up" : "down");
    $("#db-status").textContent = h.database.status === "up" ? `MySQL ${h.database.mysql_version}` : "Database down";
  }).catch(() => { $("#db-dot").className = "dot down"; $("#db-status").textContent = "Backend unreachable"; });
  api("/ai/status").then(s => {
    const ok = s.configured && s.reachable;
    $("#llm-dot").className = "dot " + (ok ? "up" : (s.configured ? "down" : ""));
    $("#llm-status").textContent = s.configured ? `${s.model} ${ok ? "" : "(unreachable → mock)"}` : "AI: mock mode";
  }).catch(() => {});
}

/* ---------------- shared widgets ---------------- */
async function fillContractSelect(select, { allowAll = false } = {}) {
  const contracts = await api("/contracts");
  select.innerHTML = (allowAll ? `<option value="">All contracts</option>` : "") +
    contracts.map(c => `<option value="${c.id}">${esc(c.name)} (${esc(c.dataset_name)}) · v${c.latest_version}</option>`).join("");
  return contracts;
}

async function fillRunSelect(select, selected) {
  const runs = await api("/runs?limit=100");
  select.innerHTML = `<option value="">Select a run…</option>` + runs.items.map(r =>
    `<option value="${esc(r.run_id)}" ${r.run_id === selected ? "selected" : ""}>${esc(r.run_id)} · ${esc(r.dataset_name)} · ${esc(r.source_file_name)} · ${esc(r.status)}</option>`).join("");
  return runs.items;
}

function runsTable(runs, { compact = false } = {}) {
  if (!runs.length) return `<div class="empty">No pipeline runs yet. Upload a batch on the <a href="ingest.html">Data Ingestion</a> page.</div>`;
  return `<div class="table-wrap"><table><thead><tr>
      <th>Run</th><th>Dataset</th><th>Source file</th><th>Status</th>
      <th class="num">Input</th><th class="num">Valid</th><th class="num">Rejected</th><th class="num">Published</th>
      ${compact ? "" : `<th class="num">Schema Δ</th>`}<th class="num">Duration</th><th>Started</th></tr></thead><tbody>
    ${runs.map(r => `<tr>
      <td>${runLink(r.run_id)}${r.attempt > 1 ? ` <span class="badge b-muted">attempt ${r.attempt}</span>` : ""}</td>
      <td>${esc(r.dataset_name)} <span class="muted">v${esc(r.contract_version)}</span></td>
      <td class="mono">${esc(r.source_file_name)}</td>
      <td>${badge(r.status)}</td>
      <td class="num">${fmtNum(r.input_count)}</td><td class="num">${fmtNum(r.valid_count)}</td>
      <td class="num">${fmtNum(r.rejected_count)}</td><td class="num">${fmtNum(r.published_count)}</td>
      ${compact ? "" : `<td class="num">${r.breaking_change_count ? `<span class="badge b-bad">${r.breaking_change_count} breaking</span> ` : ""}${r.non_breaking_change_count ? `<span class="badge b-ok">${r.non_breaking_change_count}</span>` : (r.breaking_change_count ? "" : "0")}</td>`}
      <td class="num">${fmtMs(r.duration_ms)}</td><td class="small">${fmtDate(r.started_at || r.created_at)}</td>
    </tr>`).join("")}</tbody></table></div>`;
}

function changesTable(changes) {
  if (!changes.length) return `<div class="empty">No schema changes detected - the batch matches the contract schema.</div>`;
  return `<div class="table-wrap"><table><thead><tr><th>Change</th><th>Column</th><th>Classification</th><th>Severity</th><th>Explanation</th></tr></thead><tbody>
    ${changes.map(c => `<tr><td class="mono">${esc(c.change_type)}</td><td class="mono">${esc(c.column_name || "–")}</td>
      <td>${badge(c.classification)}</td><td>${badge(c.severity)}</td><td>${esc(c.explanation)}</td></tr>`).join("")}
    </tbody></table></div>`;
}

function rejectionDetailHtml(r) {
  return `<p class="muted">Run ${runLink(r.run_id)} · file <span class="mono">${esc(r.source_file)}</span> ·
      row <strong>${esc(r.source_row_number)}</strong> · ${fmtDate(r.rejected_at)}</p>
    <h3>Rejection reasons (${r.error_count})</h3>
    <table><thead><tr><th>Code</th><th>Column</th><th>Reason</th><th>Observed value</th></tr></thead><tbody>
    ${r.errors.map(e => `<tr><td>${badge(e.error_code)}</td><td class="mono">${esc(e.column_name || "–")}</td>
      <td>${esc(e.message)}</td><td class="mono">${esc(e.observed_value ?? "NULL")}</td></tr>`).join("")}</tbody></table>
    <h3>Original record (exactly as received)</h3><pre class="json">${esc(JSON.stringify(r.original_record, null, 2))}</pre>`;
}

document.addEventListener("DOMContentLoaded", renderSidebar);
