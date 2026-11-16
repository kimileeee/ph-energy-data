// Source checks: every DOE-vs-utility comparison, with the utility's page docked beside the table and the figure
// highlighted on it. Pattern from the Naga budget portal's source viewer (a row's reference opens its scan beside the
// table, not in a modal, so a reviewer can walk the rows), plus the value's own box, which we know from the PDF.
// State lives in the hash: #u=celcor&m=residential&d=1&open=celcor-2026-10-residential
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const monthLabel = (m) => new Date(m + "-01T00:00:00").toLocaleDateString("en-PH", { month: "short", year: "numeric" });
const MEASURES = { residential: "Residential rate", generation: "Generation charge" };
const RESULT = {
  match: ["✓", "Match"],
  mismatch: ["≠", "Differs"],
  blank: ["–", "DOE blank"],
};
const resultOf = (c) => (c.status.startsWith("doe_missing") ? "blank" : c.status);
// what each kind of check compares, and what its outcome is called
const CHECK = {
  same: { label: "", words: { match: "Match", mismatch: "Differs" } },
  change: { label: "change vs. previous month", words: { match: "Match", mismatch: "Differs" } },
  floor: { label: "floor: charges before tax", words: { match: "Consistent", mismatch: "Below charges" } },
};
const wordOf = (c) => (resultOf(c) === "blank" ? RESULT.blank[1] : CHECK[c.check_type || "same"].words[resultOf(c)]);
const signed = (v, d = 2) => (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(d);
const showDoe = (c) => (c.doe_value == null ? "blank" : c.check_type === "change" ? signed(c.doe_value) : c.doe_value.toFixed(2));
const showSrc = (c) => (c.check_type === "change" ? signed(c.source_value, 4) : c.source_value.toFixed(4));
const refLabel = (c) => c.check_type === "floor" ? "schedule · formula"
  : c.read_by === "image" ? `p. ${c.page} · image`
  : `p. ${c.page} · ${c.measure === "generation" && c.utility_id === "tei" ? "Generation row" : "200 kWh row"}`;
const READABLE_PX = 1400; // page images are 150 dpi; this width keeps Meralco's ~6pt table type legible

let data, byId, rows = [];
const state = { u: "all", m: "all", d: false, open: null, zoom: "read" };

async function init() {
  data = await (await fetch("api/checks.json")).json();
  byId = new Map(data.checks.map((c) => [c.id, c]));
  $("lookback").textContent = data.lookback_months;
  $("tol").textContent = data.tolerance;

  const n = (r) => data.checks.filter((c) => resultOf(c) === r).length;
  const compared = data.checks.length - n("blank");
  $("summary").innerHTML = [
    [compared, "figures compared"], [n("match"), "match DOE"], [n("mismatch"), "differ from DOE"],
    [n("blank"), "published by the utility, still blank in DOE's sheet"],
  ].map(([v, k]) => `<div><div class="v">${v}</div><div class="k">${esc(k)}</div></div>`).join("");

  $("sources").innerHTML = data.sources.map((s) => `<div class="card src">
    <h3>${esc(s.name)}</h3>
    <p class="big">${s.matches} <span>of ${s.checks} match</span></p>
    <p class="note">${esc(s.how)}</p>
    <ul class="plain note">
      <li><a href="${esc(s.archive)}" rel="noopener">The utility's rate archive</a></li>
      ${s.api ? `<li><a href="${esc(s.api)}" rel="noopener">The archive's JSON API</a></li>` : ""}
      <li>DOE's link for it: ${s.doe_source_url ? `<a href="${esc(s.doe_source_url)}" rel="noopener">${esc(new URL(s.doe_source_url).host + new URL(s.doe_source_url).pathname)}</a>` : "none"}</li>
    </ul></div>`).join("");

  const seg = (el, opts, key) => {
    $(el).innerHTML = opts.map(([v, t]) => `<button type="button" data-v="${v}">${esc(t)}</button>`).join("");
    $(el).addEventListener("click", (e) => e.target.dataset?.v && go({ [key]: e.target.dataset.v }));
  };
  seg("fu", [["all", "All"], ...data.sources.map((s) => [s.utility_id, s.name.split(" (")[0]])], "u");
  seg("fm", [["all", "All"], ...Object.entries(MEASURES)], "m");
  $("fd").addEventListener("change", () => go({ d: $("fd").checked }));

  $("notauto").innerHTML = data.not_automated.map((x) => `<li><strong>${x.url ? `<a href="${esc(x.url)}" rel="noopener">${esc(x.name)}</a>` : esc(x.name)}.</strong> ${esc(x.why)}</li>`).join("");
  $("links").innerHTML = `<table><thead><tr><th class="l">Utility</th><th class="l">Links to</th><th class="l">Kind</th><th>HTTP</th></tr></thead><tbody>${
    data.link_audit.map((l) => `<tr><td>${esc(l.doe_name)}</td><td class="l">${l.doe_source_url ? `<a href="${esc(l.doe_source_url)}" rel="noopener">${esc(l.doe_source_url.replace(/^https?:\/\/(www\.)?/, "").slice(0, 60))}</a>` : "–"}</td><td class="l">${esc(l.kind)}</td><td>${esc(l.http_status || "not checked")}</td></tr>`).join("")}</tbody></table>`;

  $("table").addEventListener("click", (e) => {
    const id = e.target.closest("[data-open]")?.dataset.open;
    if (id) go({ open: id });
  });
  $("v-close").addEventListener("click", () => go({ open: null }));
  $("v-next").addEventListener("click", () => step(1));
  $("v-prev").addEventListener("click", () => step(-1));
  $("v-zoom").addEventListener("click", (e) => { if (e.target.dataset?.z) { state.zoom = e.target.dataset.z; renderViewer(); } });
  addEventListener("keydown", (e) => {
    if (e.target.closest("input, select, textarea")) return;
    if (e.key === "Escape" && state.open) go({ open: null });
    if (e.key === "j") step(1);
    if (e.key === "k") step(-1);
  });
  addEventListener("hashchange", () => { readHash(); render(); });
  readHash();
  render();
}

function readHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  state.u = p.get("u") || "all";
  state.m = p.get("m") || "all";
  state.d = p.get("d") === "1";
  state.open = byId.has(p.get("open")) ? p.get("open") : null;
}

function go(patch) {
  Object.assign(state, patch);
  const p = new URLSearchParams();
  if (state.u !== "all") p.set("u", state.u);
  if (state.m !== "all") p.set("m", state.m);
  if (state.d) p.set("d", "1");
  if (state.open) p.set("open", state.open);
  const h = "#" + p.toString();
  if (location.hash !== h) history.replaceState(null, "", h || location.pathname);
  render();
}

function step(d) {
  if (!rows.length) return;
  const i = rows.findIndex((c) => c.id === state.open);
  go({ open: rows[(i + d + rows.length) % rows.length].id });
}

function render() {
  for (const [el, v] of [["fu", state.u], ["fm", state.m]])
    for (const b of $(el).children) b.setAttribute("aria-pressed", b.dataset.v === v);
  $("fd").checked = state.d;

  rows = data.checks
    .filter((c) => (state.u === "all" || c.utility_id === state.u) && (state.m === "all" || c.measure === state.m) && (!state.d || resultOf(c) === "mismatch"))
    .sort((a, b) => a.utility_id.localeCompare(b.utility_id) || a.measure.localeCompare(b.measure) || b.month.localeCompare(a.month));
  $("count").textContent = `${rows.length} ${rows.length === 1 ? "comparison" : "comparisons"}`;
  $("table").innerHTML = `<table><thead><tr><th class="l">Month</th><th class="l">DU</th><th class="l">Figure</th>
    <th>DOE</th><th>Utility</th><th>Gap</th><th class="l">Result</th><th class="l">Source</th></tr></thead><tbody>${
    rows.map((c) => {
      const [icon] = RESULT[resultOf(c)];
      const kind = CHECK[c.check_type || "same"].label;
      return `<tr class="${state.open === c.id ? "active" : ""}" id="row-${c.id}">
        <td class="l nowrap">${monthLabel(c.month)}</td><td class="l">${esc(c.doe_name)}</td>
        <td class="l">${c.measure === "residential" ? "Residential" : "Generation"}${kind ? `<div class="note">${kind}</div>` : ""}</td>
        <td><a href="${esc(c.doe_cell_url)}" rel="noopener" title="Open ${esc(c.doe_cell)} in DOE's sheet">${showDoe(c)}</a></td>
        <td>${showSrc(c)}</td>
        <td>${c.abs_diff == null ? "" : c.abs_diff.toFixed(4)}</td>
        <td class="l result ${resultOf(c)}"><span aria-hidden="true">${icon}</span> ${wordOf(c)}</td>
        <td class="l"><button type="button" class="ref" data-open="${c.id}" aria-pressed="${state.open === c.id}">${refLabel(c)}</button>
          ${c.read_by === "image" ? `<span class="tag" title="Read by OCR and a vision model, then checked">read from image</span>` : ""}</td></tr>`;
    }).join("")}</tbody></table>`;
  renderViewer();
}

function renderViewer() {
  const c = state.open && byId.get(state.open);
  $("viewer").hidden = !c;
  document.body.classList.toggle("docked", !!c);
  if (!c) return;
  const [icon] = RESULT[resultOf(c)];
  const kind = CHECK[c.check_type || "same"].label;
  $("v-eyebrow").textContent = c.source_document;
  $("v-title").textContent = `${c.doe_name} · ${MEASURES[c.measure]}${kind ? ` (${kind})` : ""} · ${monthLabel(c.month)}`;
  const doeNote = c.check_type === "change" ? `DOE's figure for this month minus the previous one (${esc(c.doe_cell)})` : esc(c.doe_cell);
  const bd = c.breakdown;
  $("v-figs").innerHTML = `
    <div><div class="k">DOE's sheet</div><div class="v">${showDoe(c)}</div>
      <div class="s"><a href="${esc(c.doe_cell_url)}" rel="noopener">${doeNote}</a></div></div>
    <div><div class="k">Utility's document</div><div class="v">${showSrc(c)}</div><div class="s">${esc(c.method)}</div></div>
    <div><div class="k">Result</div><div class="v result ${resultOf(c)}"><span aria-hidden="true">${icon}</span> ${wordOf(c)}</div>
      <div class="s">${c.abs_diff == null ? "DOE has no figure to compare" : `gap ${c.abs_diff.toFixed(4)} PHP/kWh`}</div></div>
    ${c.read_by === "image" ? `<p class="note" style="grid-column:1/-1;margin:0">Read from an image by OCR and a vision model.
      ${c.check_type === "floor" ? "The figure is computed from the charges read off the schedule below." :
        c.grounded ? "The printed value was found in the OCR text (boxed on the page below)." : "<strong>The OCR did not confirm this value</strong>; check it against the page."}
      ${c.doc_status === "review" ? " This document failed an arithmetic check and is marked for review." : ""}</p>` : ""}
    ${c.explain ? `<p class="note" style="grid-column:1/-1;margin:0">${esc(c.explain)}.${c.utility_id === "celcor" ? " DOE does not publish its method; the 200 kWh bill reproduces Meralco's figures exactly." : ""}</p>` : ""}
    ${bd ? `<details class="bd" style="grid-column:1/-1" open><summary class="note">How it's computed: ${bd.kwh} kWh with the schedule's residential charges</summary>
      <table class="fields"><tbody>${Object.entries(bd.items).map(([k, v]) => `<tr><td class="l">${esc(k)}</td><td>₱${v.toFixed(2)}</td></tr>`).join("")}
      <tr><td class="l"><strong>Before VAT and local taxes</strong></td><td><strong>₱${bd.subtotal.toFixed(2)}</strong> = ${bd.rate_before_tax.toFixed(4)}/kWh</td></tr></tbody></table>
      <p class="note">VAT and the franchise tax are not on this schedule, so DOE's all-in figure should be at least this. <a href="methodology.html#formula">The formula</a></p></details>` : ""}`;
  const host = c.source_url.startsWith("http") ? new URL(c.source_url).host : "file saved by hand";
  const isPdf = /\.pdf$/i.test(c.mirror);
  $("v-links").innerHTML = `<a href="${esc(c.mirror)}${isPdf ? `#page=${c.page}` : ""}">${isPdf ? `PDF copy, p. ${c.page}` : "Image copy"}</a> · <a href="${esc(c.source_url)}" rel="noopener">Original on ${esc(host)}</a> · <a href="${esc(c.source_page_url)}" rel="noopener">Listing</a> · <span title="SHA-256 of the file as downloaded: ${esc(c.sha256)}">sha256 ${esc(c.sha256.slice(0, 10))}…</span>`;
  for (const b of $("v-zoom").children) b.setAttribute("aria-pressed", b.dataset.z === state.zoom);

  const img = $("v-img"), page = $("v-page");
  img.alt = `Page ${c.page} of ${c.source_document}, with the ${c.measure === "generation" ? "generation charge" : "residential figure"} highlighted`;
  page.style.width = state.zoom === "fit" ? "100%" : `${READABLE_PX}px`;
  const box = (el, b, pad) => Object.assign(el.style, {
    left: `${(b[0] - pad) * 100}%`, top: `${(b[1] - pad * 1.6) * 100}%`,
    width: `${(b[2] - b[0] + 2 * pad) * 100}%`, height: `${(b[3] - b[1] + 3.2 * pad) * 100}%`,
  });
  $("v-row").hidden = !c.row_box;
  $("v-val").hidden = !c.value_box;
  if (c.row_box) box($("v-row"), c.row_box, 0.003);
  if (c.value_box) box($("v-val"), c.value_box, 0.004);
  $("v-rowtext").textContent = c.row_text ? `${c.read_by === "image" ? "OCR text" : "Row as extracted"}: ${c.row_text}` : "";
  const center = () => {
    const doc = $("v-doc"), v = $("v-val");
    if (v.hidden) return doc.scrollTo({ left: 0, top: 0 });
    doc.scrollTo({ left: v.offsetLeft - doc.clientWidth / 2 + v.offsetWidth / 2, top: v.offsetTop - doc.clientHeight / 2, behavior: "instant" });
  };
  if (img.getAttribute("src") !== c.image) { img.onload = center; img.src = c.image; } else center();
  document.getElementById(`row-${c.id}`)?.scrollIntoView({ block: "nearest" });
}

init().catch((e) => { $("summary").textContent = `Could not load the checks: ${e.message}`; });
