/* Quarantine browser: filter by run / error type / contract, view details, export CSV. */
"use strict";

const PAGE = 50;
let offset = 0;
let items = [];

function filters() {
  const q = new URLSearchParams();
  if ($("#f-run").value) q.set("run_id", $("#f-run").value);
  if ($("#f-code").value) q.set("error_code", $("#f-code").value);
  if ($("#f-contract").value) q.set("contract_id", $("#f-contract").value);
  return q;
}

async function load() {
  const q = filters();
  $("#export").href = `${API}/rejected/export?${q}`;
  history.replaceState(null, "", "?" + q);
  q.set("limit", PAGE); q.set("offset", offset);
  const box = $("#table");
  let res;
  try { res = await api(`/rejected?${q}`); } catch (e) { return showError(box, e); }
  items = res.items;
  if (!items.length) { box.innerHTML = `<div class="empty">No rejected records match these filters.</div>`; return; }
  box.innerHTML = `<div class="table-wrap"><table><thead><tr>
      <th>Run</th><th>Source file</th><th class="num">Row</th><th>Error codes</th><th>Rejection reasons</th><th></th></tr></thead><tbody>
    ${items.map((r, i) => `<tr class="clickable" data-i="${i}">
      <td>${runLink(r.run_id)}</td><td class="mono small">${esc(r.source_file)}</td><td class="num">${r.source_row_number}</td>
      <td>${chips(r.error_codes)}</td>
      <td class="small">${r.errors.slice(0, 3).map(e => `${esc(e.message)}${e.observed_value !== null ? ` <span class="muted">(value: <span class="mono">${esc(e.observed_value)}</span>)</span>` : ""}`).join("<br>")}${r.errors.length > 3 ? `<br><span class="muted">+${r.errors.length - 3} more</span>` : ""}</td>
      <td><button class="btn secondary small">Details</button></td></tr>`).join("")}</tbody></table></div>
    <div class="pager"><span class="muted small">${fmtNum(offset + 1)}–${fmtNum(offset + items.length)} of ${fmtNum(res.total)}</span>
      <span><button class="btn secondary small" id="prev" ${offset ? "" : "disabled"}>Prev</button>
      <button class="btn secondary small" id="next" ${offset + PAGE < res.total ? "" : "disabled"}>Next</button></span></div>`;
  box.querySelectorAll("tr.clickable").forEach(tr => tr.addEventListener("click", async () => {
    const r = items[+tr.dataset.i];
    try { modal(`Rejected record #${r.id}`, rejectionDetailHtml(await api(`/rejected/${r.id}`))); }
    catch (e) { toast(e.message, true); }
  }));
  $("#prev")?.addEventListener("click", () => { offset = Math.max(0, offset - PAGE); load(); });
  $("#next")?.addEventListener("click", () => { offset += PAGE; load(); });
}

document.addEventListener("DOMContentLoaded", async () => {
  const p = params();
  try {
    await fillRunSelect($("#f-run"), p.get("run_id"));
    $("#f-run").options[0].textContent = "All runs";
    await fillContractSelect($("#f-contract"), { allowAll: true });
    if (p.get("contract_id")) $("#f-contract").value = p.get("contract_id");
    const codes = await api("/error-codes");
    $("#f-code").innerHTML += codes.map(c => `<option value="${esc(c.error_code)}">${esc(c.error_code)}</option>`).join("");
    if (p.get("error_code")) $("#f-code").value = p.get("error_code");
  } catch (e) { return showError($("#table"), e); }
  ["#f-run", "#f-code", "#f-contract"].forEach(s => $(s).addEventListener("change", () => { offset = 0; load(); }));
  load();
});
