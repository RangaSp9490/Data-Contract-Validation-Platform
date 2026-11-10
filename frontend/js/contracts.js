/* Contract management page. */
"use strict";

let contracts = [];
let current = null;          // ContractDetail
let activeTab = "columns";
let publishedOffset = 0;

async function loadList(selectId) {
  try {
    contracts = await api("/contracts");
  } catch (e) { return showError($("#error"), e); }
  const list = $("#contract-list");
  if (!contracts.length) {
    list.innerHTML = `<div class="empty">No contracts yet.<br>Click <strong>New contract</strong> and load a sample template.</div>`;
    return;
  }
  list.innerHTML = contracts.map(c => `<div class="list-item" data-id="${c.id}">
      <strong>${esc(c.name)}</strong> <span class="badge b-info">v${c.latest_version}</span>
      <div class="small muted mono">${esc(c.dataset_name)}</div>
      <div class="small muted">${esc(c.owner || "")}</div></div>`).join("");
  list.querySelectorAll(".list-item").forEach(el => el.addEventListener("click", () => selectContract(+el.dataset.id)));
  const target = selectId || +params().get("id") || contracts[0].id;
  selectContract(target);
}

async function selectContract(id, version) {
  document.querySelectorAll(".list-item").forEach(el => el.classList.toggle("active", +el.dataset.id === id));
  try {
    current = await api(`/contracts/${id}` + (version ? `?version=${version}` : ""));
  } catch (e) { return showError($("#detail"), e); }
  history.replaceState(null, "", `?id=${id}`);
  publishedOffset = 0;
  renderDetail();
}

function renderDetail() {
  const c = current, v = c.current;
  $("#detail").innerHTML = `
    <div class="page-head" style="margin-bottom:8px">
      <div><h1 style="font-size:19px">${esc(c.name)} <span class="mono muted" style="font-size:14px">${esc(c.dataset_name)}</span></h1>
        <p>${esc(c.description || "")}</p></div>
      <div class="row shrink"><label class="shrink" style="margin:0">Version</label>
        <select id="version-select" style="width:auto">${c.versions.map(n =>
          `<option value="${n}" ${n === v.version ? "selected" : ""}>v${n}${n === c.latest_version ? " (latest)" : ""}</option>`).join("")}</select>
        <button class="btn danger small shrink" id="delete-contract" title="Delete this contract permanently">Delete</button></div>
    </div>
    <div id="delete-result"></div>
    <div class="row small muted" style="margin-bottom:10px">
      <span>Policy ${badge(v.compatibility_mode)}</span><span>Primary key ${chips(v.primary_key)}</span>
      <span>Event time <span class="chip">${esc(v.event_time_column || "none")}</span></span>
      <span>Created ${fmtDate(v.created_at)} ${v.created_by ? "by " + esc(v.created_by) : ""}</span></div>
    <div class="tabs">${[["columns", "Columns"], ["rules", "Rules & keys"], ["changes", `Changes in v${v.version}`],
        ["published", "Published data"], ["edit", "New version"]].map(([k, l]) =>
        `<button data-tab="${k}" class="${k === activeTab ? "active" : ""}">${l}</button>`).join("")}</div>
    <div id="tab-body"></div>`;
  $("#version-select").addEventListener("change", e => selectContract(c.id, +e.target.value));
  $("#delete-contract").addEventListener("click", () => deleteContract(c));
  document.querySelectorAll(".tabs button").forEach(b => b.addEventListener("click", () => { activeTab = b.dataset.tab; renderDetail(); }));
  ({ columns: renderColumns, rules: renderRules, changes: renderChanges, published: renderPublished, edit: renderEditor })[activeTab]();
}

function renderColumns() {
  const v = current.current;
  $("#tab-body").innerHTML = `<div class="table-wrap"><table><thead><tr>
    <th>#</th><th>Column</th><th>Type</th><th>Required</th><th>Nullable</th><th>PK</th><th>Allowed values</th>
    <th>Default</th><th>Range</th><th>Format</th><th>Normalize</th></tr></thead><tbody>
    ${v.columns.map(col => `<tr><td class="muted">${col.ordinal}</td>
      <td class="mono"><strong>${esc(col.name)}</strong>${col.renamed_from ? `<div class="small muted">was ${esc(col.renamed_from)}</div>` : ""}
        ${col.description ? `<div class="small muted">${esc(col.description)}</div>` : ""}</td>
      <td>${esc(col.data_type)}</td><td>${col.required ? "yes" : "no"}</td><td>${col.nullable ? "yes" : "no"}</td>
      <td>${col.is_primary_key ? badge("PK") : ""}</td><td>${col.allowed_values ? chips(col.allowed_values) : "<span class='muted'>any</span>"}</td>
      <td class="mono">${col.default_value !== null && col.default_value !== undefined ? esc(col.default_value) : ""}</td>
      <td class="small">${col.min_value ?? ""}${col.min_value !== null || col.max_value !== null ? " … " : ""}${col.max_value ?? ""}</td>
      <td class="small mono">${esc(col.pattern || "")}${col.max_length ? ` (max ${col.max_length})` : ""}</td>
      <td>${esc(col.normalize || "")}</td></tr>`).join("")}</tbody></table></div>`;
}

function ruleText(r) {
  if (r.type === "compare_columns") return `${r.left} ${r.op} ${r.right}`;
  if (r.type === "product_equals") return `${r.target} = ${r.factors.join(" × ")} (± ${r.tolerance})`;
  if (r.type === "not_in_future") return `${r.column} ≤ now + ${r.tolerance_minutes} min`;
  return JSON.stringify(r);
}

function renderRules() {
  const v = current.current;
  $("#tab-body").innerHTML = `
    <h3>Primary key</h3>${chips(v.primary_key)}
    <h3>Unique constraints (business events)</h3>${v.unique_constraints.length ? v.unique_constraints.map(g => `<div>${chips(g)}</div>`).join("") : "<span class='muted'>none</span>"}
    <h3>Foreign keys (referential integrity)</h3>${v.foreign_keys.length ? `<table><tbody>${v.foreign_keys.map(f =>
      `<tr><td class="mono">${esc(f.column)}</td><td>→</td><td class="mono">${esc(f.ref_dataset)}.${esc(f.ref_column)}</td><td class="muted small">must exist in published ${esc(f.ref_dataset)} data</td></tr>`).join("")}</tbody></table>` : "<span class='muted'>none</span>"}
    <h3>Business rules</h3>${v.business_rules.length ? `<table><thead><tr><th>Name</th><th>Type</th><th>Rule</th><th>Description</th></tr></thead><tbody>
      ${v.business_rules.map(r => `<tr><td class="mono">${esc(r.name)}</td><td>${esc(r.type)}</td><td class="mono">${esc(ruleText(r))}</td><td>${esc(r.description || "")}</td></tr>`).join("")}</tbody></table>` : "<span class='muted'>none</span>"}
    <h3>Change notes</h3><p>${esc(v.change_notes || "–")}</p>`;
}

function renderChanges() {
  const v = current.current;
  $("#tab-body").innerHTML = v.version === 1
    ? `<div class="empty">Version 1 is the initial contract - there is no previous version to compare with.</div>`
    : `<p class="muted">Classified differences between v${v.version - 1} and v${v.version} (policy of v${v.version - 1} applies).</p>${changesTable(v.schema_changes)}`;
}

async function renderPublished() {
  const body = $("#tab-body");
  body.innerHTML = `<div class="filters"><div><label>Search primary key</label><input type="text" id="pk-search" placeholder="e.g. ORD-0000"></div>
    <div class="shrink" style="min-width:0"><a class="btn secondary" href="${API}/valid-records/export?contract_id=${current.id}">⇩ Download CSV</a></div></div>
    <div id="pub-table"><span class="spinner"></span></div>`;
  const load = async () => {
    const q = $("#pk-search").value.trim();
    try {
      const res = await api(`/valid-records?contract_id=${current.id}&limit=25&offset=${publishedOffset}` + (q ? `&search=${encodeURIComponent(q)}` : ""));
      const cols = current.current.columns.map(c => c.name);
      $("#pub-table").innerHTML = !res.items.length ? `<div class="empty">No published records${q ? " match" : " yet"}.</div>` :
        `<div class="table-wrap"><table><thead><tr>${cols.map(c => `<th>${esc(c)}</th>`).join("")}<th>Last run</th></tr></thead><tbody>
        ${res.items.map(r => `<tr>${cols.map(c => `<td class="small mono">${esc(r.payload[c] ?? "")}</td>`).join("")}<td>${runLink(r.last_run_id)}</td></tr>`).join("")}
        </tbody></table></div>
        <div class="pager"><span class="muted small">${fmtNum(publishedOffset + 1)}–${fmtNum(publishedOffset + res.items.length)} of ${fmtNum(res.total)}</span>
        <span><button class="btn secondary small" id="pg-prev" ${publishedOffset ? "" : "disabled"}>Prev</button>
        <button class="btn secondary small" id="pg-next" ${publishedOffset + 25 < res.total ? "" : "disabled"}>Next</button></span></div>`;
      $("#pg-prev")?.addEventListener("click", () => { publishedOffset = Math.max(0, publishedOffset - 25); load(); });
      $("#pg-next")?.addEventListener("click", () => { publishedOffset += 25; load(); });
    } catch (e) { showError($("#pub-table"), e); }
  };
  let t;
  $("#pk-search").addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { publishedOffset = 0; load(); }, 300); });
  load();
}

function specFromVersion(v) {
  const clean = o => Object.fromEntries(Object.entries(o).filter(([k, val]) => val !== null && k !== "ordinal"));
  return {
    compatibility_mode: v.compatibility_mode, event_time_column: v.event_time_column,
    columns: v.columns.map(clean), unique_constraints: v.unique_constraints, foreign_keys: v.foreign_keys,
    business_rules: v.business_rules, change_notes: "", created_by: "",
  };
}

function renderEditor() {
  const latest = current.latest_version, v = current.current;
  $("#tab-body").innerHTML = `
    ${v.version !== latest ? `<div class="alert warn">You are viewing v${v.version}; the new version is compared with the latest (v${latest}).</div>` : ""}
    <p class="muted">Edit the JSON specification below. <strong>Check compatibility</strong> is a dry run; <strong>Create version</strong> stores v${latest + 1}. v${latest} is never modified.</p>
    <textarea id="spec" spellcheck="false">${esc(JSON.stringify(specFromVersion(v), null, 2))}</textarea>
    <label class="check"><input type="checkbox" id="ack"> I acknowledge breaking changes (required if the new version is not backward compatible)</label>
    <div class="row shrink"><button class="btn secondary shrink" id="check">Check compatibility</button><button class="btn shrink" id="create">Create version v${latest + 1}</button></div>
    <div id="edit-result"></div>`;
  const parseSpec = () => {
    try { return JSON.parse($("#spec").value); }
    catch (e) { showError($("#edit-result"), "Invalid JSON: " + e.message); return null; }
  };
  $("#check").addEventListener("click", async () => {
    const spec = parseSpec(); if (!spec) return;
    try {
      const rep = await api(`/contracts/${current.id}/compatibility-check`, { method: "POST", body: spec });
      $("#edit-result").innerHTML = compatSummary(rep) + changesTable(rep.changes);
    } catch (e) { showError($("#edit-result"), e); }
  });
  $("#create").addEventListener("click", async () => {
    const spec = parseSpec(); if (!spec) return;
    spec.acknowledge_breaking = $("#ack").checked;
    try {
      const res = await api(`/contracts/${current.id}/versions`, { method: "POST", body: spec });
      toast(`Version v${res.version.version} created (${res.compatibility.breaking_count} breaking change(s))`);
      activeTab = "changes";
      await loadList(current.id);
    } catch (e) {
      if (e.status === 409 && e.body && e.body.details) {
        $("#edit-result").innerHTML = `<div class="alert bad">${esc(e.message)}</div>` + changesTable(e.body.details.changes);
      } else showError($("#edit-result"), e);
    }
  });
}

async function deleteContract(c) {
  let p;
  try { p = await api(`/contracts/${c.id}/delete-preview`); }
  catch (e) { toast(e.message, true); return; }

  const blocked = !p.can_delete;
  const m = modal(blocked ? "Cannot delete contract" : "Delete permanently?", blocked
    ? `<div class="alert warn">Contract <strong>${esc(p.name)}</strong> (<span class="mono">${esc(p.dataset_name)}</span>)
         is used by <strong>${esc(p.referenced_by.join(", "))}</strong> through a foreign key.
         Delete ${esc(p.referenced_by.join(" and "))} first, then delete this contract.</div>
       <div class="row shrink" style="justify-content:flex-end"><button class="btn secondary shrink" data-close>OK</button></div>`
    : `<div class="alert bad"><strong>⚠ Warning: this cannot be undone.</strong><br>
         Contract <strong>${esc(p.name)}</strong> (<span class="mono">${esc(p.dataset_name)}</span>) and everything that
         belongs to it will be deleted permanently from the database.</div>
       <table class="small" style="margin:10px 0"><tbody>
         <tr><td>Contract versions</td><td class="num"><strong>${fmtNum(p.versions)}</strong></td></tr>
         <tr><td>Pipeline runs</td><td class="num"><strong>${fmtNum(p.pipeline_runs)}</strong></td></tr>
         <tr><td>Published records</td><td class="num"><strong>${fmtNum(p.published_records)}</strong></td></tr>
         <tr><td>Rejected (quarantined) records</td><td class="num"><strong>${fmtNum(p.rejected_records)}</strong></td></tr>
       </tbody></table>
       <div class="row shrink" style="justify-content:flex-end;gap:10px">
         <button class="btn secondary shrink" data-close>Cancel</button>
         <button class="btn danger shrink" id="confirm-delete">Delete permanently</button></div>
       <div id="confirm-result"></div>`);

  $("#confirm-delete", m)?.addEventListener("click", async (ev) => {
    ev.target.disabled = true;
    ev.target.textContent = "Deleting…";
    try {
      const res = await api(`/contracts/${c.id}`, { method: "DELETE" });
      m.remove();
      toast(`Contract '${res.dataset_name}' deleted permanently (${fmtNum(res.pipeline_runs)} runs, ` +
            `${fmtNum(res.published_records)} published, ${fmtNum(res.rejected_records)} rejected records removed)`);
      current = null;
      history.replaceState(null, "", location.pathname);
      $("#detail").innerHTML = `<div class="empty">Select a contract, or create one.</div>`;
      contracts = contracts.filter(x => x.id !== c.id);
      loadList(contracts.length ? contracts[0].id : undefined);
    } catch (e) {
      showError($("#confirm-result", m), e);
      ev.target.disabled = false;
      ev.target.textContent = "Delete permanently";
    }
  });
}

function compatSummary(rep) {
  return `<div class="alert ${rep.compatible ? "ok" : "bad"}">${rep.compatible ? "Backward compatible" : "NOT compatible"} with v${rep.from_version}:
    ${rep.breaking_count} breaking, ${rep.non_breaking_count} non-breaking change(s).</div>`;
}

async function newContractDialog() {
  let templates = {};
  try { templates = await api("/contracts/templates"); } catch (e) { /* editor still usable */ }
  const blank = { name: "", dataset_name: "", description: "", owner: "", compatibility_mode: "BACKWARD", event_time_column: null,
    columns: [{ name: "id", data_type: "string", is_primary_key: true }], unique_constraints: [], foreign_keys: [], business_rules: [] };
  const m = modal("Create contract", `
    <p class="muted">Define the contract as JSON (creates version 1). Templates load the demo e-commerce contracts - create <strong>customers</strong> before <strong>orders</strong> (orders references customers).</p>
    <div class="row"><select id="tpl"><option value="">Blank template</option>${Object.keys(templates).map(k => `<option value="${esc(k)}">Sample: ${esc(k)}</option>`).join("")}</select></div>
    <textarea id="new-spec" spellcheck="false" style="margin-top:10px">${esc(JSON.stringify(blank, null, 2))}</textarea>
    <div class="row shrink" style="margin-top:10px"><button class="btn shrink" id="create-contract">Create contract</button></div><div id="new-result"></div>`);
  $("#tpl", m).addEventListener("change", e => {
    $("#new-spec", m).value = JSON.stringify(e.target.value ? templates[e.target.value] : blank, null, 2);
  });
  $("#create-contract", m).addEventListener("click", async () => {
    let spec;
    try { spec = JSON.parse($("#new-spec", m).value); } catch (e) { return showError($("#new-result", m), "Invalid JSON: " + e.message); }
    try {
      const created = await api("/contracts", { method: "POST", body: spec });
      m.remove();
      toast(`Contract '${created.dataset_name}' created`);
      activeTab = "columns";
      loadList(created.id);
    } catch (e) { showError($("#new-result", m), e); }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  loadList();
  $("#new-contract").addEventListener("click", newContractDialog);
});
