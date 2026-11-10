/* Upload a batch (async run) and poll its status until it reaches a terminal state. */
"use strict";

const TERMINAL = ["COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED", "SKIPPED_DUPLICATE"];
let contractsCache = [];
let selectedFile = null;

async function loadVersions() {
  const c = contractsCache.find(x => x.id === +$("#contract").value);
  $("#version").innerHTML = c ? c.versions.slice().reverse().map(n =>
    `<option value="${n}">v${n}${n === c.latest_version ? " (latest)" : ""}</option>`).join("") : "";
}

function setFile(file) {
  if (!file) return;
  const ext = file.name.split(".").pop().toLowerCase();
  if (!["csv", "json"].includes(ext)) { toast("Only .csv and .json files are supported", true); return; }
  selectedFile = file;
  $("#drop-text").innerHTML = `<strong>${esc(file.name)}</strong><br>${fmtNum(file.size)} bytes · click to change`;
}

function stepsHtml(status) {
  const order = ["PENDING", "RUNNING", "DONE"];
  const at = status === "PENDING" ? 0 : status === "RUNNING" ? 1 : 2;
  const fail = status === "FAILED";
  const labels = ["Queued", "Parsing · schema check · validation · publishing", fail ? "Failed" : status === "SKIPPED_DUPLICATE" ? "Skipped (duplicate)" : "Finished"];
  return `<div class="steps">${order.map((_, i) => `<span class="step ${i < at ? "done" : i === at ? (i === 2 ? (fail ? "fail" : "done") : "now") : ""}">${i === at && at < 2 ? '<span class="spinner"></span> ' : ""}${labels[i]}</span>`).join("")}</div>`;
}

async function poll(runId) {
  const box = $("#status");
  for (let i = 0; i < 1800; i++) {            // up to ~30 min for very large files
    let run;
    try { run = await api(`/runs/${encodeURIComponent(runId)}`); }
    catch (e) { return showError(box, e); }
    box.innerHTML = `<p>Run ${runLink(run.run_id)} · ${badge(run.status)}</p>${stepsHtml(run.status)}
      <p class="small muted">File <span class="mono">${esc(run.source_file_name)}</span> · checksum <span class="mono">${esc((run.source_checksum || "").slice(0, 16))}…</span></p>`;
    if (TERMINAL.includes(run.status)) {
      box.innerHTML += runSummaryHtml(run) +
        `<div class="row shrink" style="margin-top:12px"><a class="btn shrink" href="validation.html?run_id=${encodeURIComponent(run.run_id)}">Open validation report</a>
         ${run.rejected_count ? `<a class="btn secondary shrink" href="rejected.html?run_id=${encodeURIComponent(run.run_id)}">View ${fmtNum(run.rejected_count)} rejected</a>` : ""}</div>`;
      $("#submit").disabled = false;
      return;
    }
    await new Promise(r => setTimeout(r, 1000));
  }
}

async function submit(e) {
  e.preventDefault();
  if (!selectedFile) { toast("Choose a file first", true); return; }
  const form = new FormData();
  form.append("file", selectedFile);
  form.append("contract_id", $("#contract").value);
  form.append("version", $("#version").value);
  if ($("#source").value.trim()) form.append("source_system", $("#source").value.trim());
  form.append("force", $("#force").checked ? "true" : "false");
  $("#submit").disabled = true;
  $("#status").innerHTML = `<p><span class="spinner"></span> Uploading…</p>`;
  try {
    const run = await api("/ingestion/upload", { method: "POST", body: form });
    poll(run.run_id);
  } catch (err) {
    showError($("#status"), err);
    $("#submit").disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  try {
    contractsCache = await fillContractSelect($("#contract"));
    if (!contractsCache.length) {
      $("#error").innerHTML = `<div class="alert warn">No contracts exist yet. <a href="contracts.html">Create a contract</a> first.</div>`;
    }
    const pre = params().get("contract_id");
    if (pre) $("#contract").value = pre;
    loadVersions();
  } catch (e) { showError($("#error"), e); }
  $("#contract").addEventListener("change", loadVersions);
  const drop = $("#drop"), input = $("#file");
  drop.addEventListener("click", () => input.click());
  drop.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") input.click(); });
  input.addEventListener("change", () => setFile(input.files[0]));
  ["dragenter", "dragover"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("drag"); }));
  ["dragleave", "drop"].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove("drag"); }));
  drop.addEventListener("drop", e => setFile(e.dataTransfer.files[0]));
  $("#upload-form").addEventListener("submit", submit);
});
