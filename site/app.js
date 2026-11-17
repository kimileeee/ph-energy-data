// "Your utility, your price": Chart | Map | Table over one view held in the URL hash
// (#du=meralco&class=residential&view=map&month=2026-10[&measure=saidi]). Reads the static JSON API next to this page. No dependencies.
const CLASSES = { residential: "Residential", commercial: "Commercial", industrial: "Industrial", generation: "Generation charge" };
const VIEWS = ["chart", "map", "table"];
const MIN_PEERS = 3; // a "regional median" of one or two utilities is just those utilities
// fixed bins so colours mean the same thing in every month; generation charge runs lower than retail rates
const BINS = { retail: [10, 12, 14, 16], generation: [5, 6.5, 8, 9.5], saidi: [4, 8, 16, 32] }; // saidi: hours a year
const MEASURES = ["rate", "saidi"];
const $ = (id) => document.getElementById(id);
const fmt = (v) => (v == null ? "–" : v.toFixed(2));
const monthLabel = (m) => new Date(m + "-01T00:00:00").toLocaleDateString("en-PH", { month: "short", year: "numeric" });
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const median = (xs) => {
  if (!xs.length) return null;
  const s = [...xs].sort((a, b) => a - b), h = s.length >> 1;
  return s.length % 2 ? s[h] : (s[h - 1] + s[h]) / 2;
};

let utilities, byId, medians, panel, months, geo, current, rel;
const state = { du: "meralco", klass: "residential", view: "chart", month: null, place: null, base: true, measure: "rate" };

async function json(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

async function init() {
  [{ utilities }, medians, panel, rel] = await Promise.all(
    ["api/utilities.json", "api/medians.json", "api/panel.json", "api/reliability.json"].map(json));
  byId = new Map(utilities.map((u) => [u.utility_id, u]));
  // months the sheet actually has figures for (the newest column can be empty until DOE compiles it)
  months = panel.months.filter((m, i) => Object.values(panel.series.residential).some((s) => s[i] != null));

  const byRegion = Map.groupBy(utilities, (u) => u.region);
  $("du").innerHTML = [...byRegion.keys()].sort((a, b) => regionRank(a) - regionRank(b))
    .map((r) => `<optgroup label="${esc(r)}">${byRegion.get(r).sort((a, b) => a.doe_name.localeCompare(b.doe_name))
      .map((u) => `<option value="${u.utility_id}">${esc(u.doe_name)}${u.name ? " · " + esc(u.name) : ""}</option>`).join("")}</optgroup>`).join("");
  $("classes").innerHTML = Object.entries(CLASSES).map(([k, v]) => `<button type="button" data-k="${k}">${v}</button>`).join("");
  $("classes").addEventListener("click", (e) => e.target.dataset?.k && go({ klass: e.target.dataset.k }));
  $("du").addEventListener("change", () => go({ du: $("du").value }));
  $("measures").addEventListener("click", (e) => e.target.dataset?.m && go({ measure: e.target.dataset.m }));

  const tabs = [...document.querySelectorAll('[role="tab"]')];
  tabs.forEach((t, i) => {
    t.addEventListener("click", () => go({ view: VIEWS[i] }));
    t.addEventListener("keydown", (e) => {
      const d = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
      if (!d) return;
      const n = (i + d + tabs.length) % tabs.length;
      go({ view: VIEWS[n] });
      tabs[n].focus();
    });
  });

  $("month").max = months.length - 1;
  $("month").addEventListener("input", () => go({ month: months[+$("month").value] }, true));

  let raf;
  addEventListener("resize", () => { cancelAnimationFrame(raf); raf = requestAnimationFrame(() => current && state.view === "chart" && renderChart()); });
  addEventListener("hashchange", () => { readHash(); load(); });
  readHash();
  load();
}

function readHash() {
  const h = location.hash.slice(1);
  const p = new URLSearchParams(h.includes("=") ? h : h ? `du=${h}` : ""); // "#meralco" still works
  const next = {
    du: byId.has(p.get("du")) ? p.get("du") : state.du,
    klass: CLASSES[p.get("class")] ? p.get("class") : state.klass,
    view: VIEWS.includes(p.get("view")) ? p.get("view") : state.view,
    month: months.includes(p.get("month")) ? p.get("month") : state.month || months.at(-1),
    place: /^\d{10}$/.test(p.get("place") || "") ? p.get("place") : null,
    base: p.get("base") !== "0",
    measure: MEASURES.includes(p.get("measure")) ? p.get("measure") : "rate",
  };
  Object.assign(state, next);
}

// every control writes the hash; the hash is the only state (back/forward and shared links just work)
function go(patch, replace = false) {
  Object.assign(state, patch);
  const h = `#du=${state.du}&class=${state.klass}&view=${state.view}&month=${state.month}${state.place ? `&place=${state.place}` : ""}${state.base ? "" : "&base=0"}${state.measure === "rate" ? "" : `&measure=${state.measure}`}`;
  if (replace) { history.replaceState(null, "", h); load(); }
  else if (location.hash !== h) location.hash = h;
  else load();
}

// NCR, CAR, Region 1..12, NIR, CARAGA, BARMM in the usual reading order
function regionRank(r) {
  const order = ["NCR", "CAR", "Region 1", "Region 2", "Region 3", "Region 4A", "Region 4B", "Region 5", "Region 6", "NIR",
    "Region 7", "Region 8", "Region 9", "Region 10", "Region 11", "Region 12", "CARAGA", "BARMM"];
  const i = order.indexOf(r);
  return i < 0 ? 99 : i;
}

async function load() {
  const u = byId.get(state.du);
  if (current?.u !== u) current = { u, rates: await json(`api/rates/${u.utility_id}.json`) };
  $("du").value = u.utility_id;
  for (const b of $("classes").children) b.setAttribute("aria-pressed", b.dataset.k === state.klass);
  document.querySelectorAll('[role="tab"]').forEach((t, i) => {
    const on = VIEWS[i] === state.view;
    t.setAttribute("aria-selected", on);
    t.tabIndex = on ? 0 : -1;
    $(t.getAttribute("aria-controls")).hidden = !on;
  });
  $("monthbar").hidden = state.view === "chart" || (state.view === "map" && state.measure === "saidi"); // NEA's figure is yearly
  $("month").value = months.indexOf(state.month);
  $("monthlabel").textContent = monthLabel(state.month);
  renderSummary();
  renderReliability();
  if (state.view === "chart") renderChart();
  if (state.view === "map") await renderMap();
  if (state.view === "table") renderTable();
}

function renderSummary() {
  const { u, rates } = current, klass = state.klass;
  const cities = u.independent_cities ? u.independent_cities.split(";") : [];
  const serves = [u.psgc_provinces ? u.psgc_provinces.split(";").join(", ") : "",
    cities.length ? (cities.length > 3 ? `${cities.length} independent cities (${cities.slice(0, 3).join(", ")}…)` : cities.join(", ")) : ""]
    .filter(Boolean).join("; ") || "not listed in the coverage source";
  $("meta").innerHTML = `<strong>${esc(u.name || u.doe_name)}</strong> · ${u.du_type === "EC" ? "Electric cooperative" : "Private utility (PIOU)"} · ${esc(u.grid.toLowerCase())} · ${esc(u.region)}, ${esc(u.island)}<br>Serves: ${esc(serves)}${u.coverage_note ? ` <span class="note">(${esc(u.coverage_note)})</span>` : ""}` +
    (u.doe_source_url ? ` · <a href="${esc(u.doe_source_url)}" rel="noopener">DOE's source</a>` : "");

  const own = rates.series[klass], last = own.at(-1), med = medianSeries();
  const tiles = [{ k: u.doe_name, sw: "var(--series-1)", v: last?.[1], s: last ? monthLabel(last[0]) : "no data" }];
  if (med && last) {
    const r = med.region?.[last[0]], n = med.national[last[0]];
    if (med.region) tiles.push({ k: `${u.region} median`, sw: "var(--series-2)", v: r, s: `${monthLabel(last[0])} · ${med.peers} utilities in region` });
    tiles.push({ k: "National median", sw: "var(--series-3)", v: n, s: `${monthLabel(last[0])} · ${medians.counts[klass][last[0]]} utilities` });
    if (n) tiles.push({ k: "Versus national", txt: `${last[1] >= n ? "+" : "−"}${Math.abs(((last[1] - n) / n) * 100).toFixed(0)}%`, s: `${fmt(Math.abs(last[1] - n))} PHP/kWh ${last[1] >= n ? "above" : "below"}` });
  }
  $("tiles").innerHTML = tiles.map((t) => `<div class="tile"><div class="k">${t.sw ? `<i class="sw" style="background:${t.sw}"></i>` : ""}${esc(t.k)}</div>
    <div class="v">${t.txt ?? fmt(t.v)}</div><div class="s">${esc(t.s)}</div></div>`).join("");
}

// ---------- Power interruptions (NEA, electric cooperatives) ----------

const hours = (min) => (min == null ? "–" : `${(min / 60).toFixed(1)} h`);
const quarterLabel = (p) => `${p.slice(5)} ${p.slice(0, 4)}`;
const relRows = (uid) => rel.utilities[uid] || [];
const fullYear = (uid) => relRows(uid).find((r) => r.period === rel.full_year_period) || null;
// the place a figure stands: hours without power per customer in the last full year, or null (private utility, or none sent)
const saidiHours = (uid) => { const r = fullYear(uid); return r?.saidi_minutes == null ? null : r.saidi_minutes / 60; };

function renderReliability() {
  const u = current.u, box = $("rel");
  if (u.du_type !== "EC") {
    box.innerHTML = `<p>${esc(u.doe_name)} is a private utility. ERC, which regulates private utilities, doesn't publish how often their customers lose power; NEA's reports cover electric cooperatives only.</p>`;
    return;
  }
  const rows = relRows(u.utility_id);
  if (!rows.length) { box.innerHTML = `<p class="note">NEA's reports don't list ${esc(u.doe_name)}.</p>`; return; }
  const std = rel.standards[u.grid === "OFFGRID" ? "off_grid" : "on_grid"], fy = fullYear(u.utility_id), med = rel.medians[rel.full_year_period];
  const latest = rows.at(-1), y = rel.full_year;
  const tiles = [];
  if (fy?.saifi != null) {
    tiles.push({ k: `Interruptions per customer, ${y}`, v: fy.saifi.toFixed(1), s: `Cooperative median ${med.saifi.toFixed(1)} · NEA standard ${std.saifi}` });
    tiles.push({ k: `Hours without power per customer, ${y}`, v: hours(fy.saidi_minutes), s: `Median ${hours(med.saidi_minutes)} · standard ${hours(std.saidi_minutes)}` });
  }
  if (latest.period !== rel.full_year_period && latest.saifi != null)
    tiles.push({ k: `So far in ${latest.period.slice(0, 4)} (to ${latest.period.slice(5)})`, v: hours(latest.saidi_minutes), s: `${latest.saifi.toFixed(1)} interruptions per customer` });
  const flags = fy && !fy.missing ? [fy.saifi_status === "NC" && "interruptions", fy.saidi_status === "NC" && "hours"].filter(Boolean) : [];
  const notes = [
    !fy ? `NEA's ${quarterLabel(rel.full_year_period)} report doesn't list ${esc(u.doe_name)}.` : "",
    fy?.missing ? `${esc(u.doe_name)} sent NEA no figures for ${y} (NEA prints “${esc(fy.missing)}”), and NEA counts that as not meeting the standard.` : "",
    flags.length ? `NEA rates ${esc(u.doe_name)} below its standard on ${flags.join(" and ")} for ${y}.` : "",
    ...(rel.issues[u.utility_id] || []).map((t) => `Question for NEA: ${esc(t)}; a year-to-date figure can't fall within a year.`),
  ].filter(Boolean);
  box.innerHTML = `<p class="note">From NEA's quarterly compliance reports. The figures run year to date, so ${y}'s full year is its Q4 figure. Medians are across cooperatives with a figure, unweighted. <a href="methodology.html#interruptions">How these are read</a>.</p>
    ${tiles.length ? `<div class="tiles">${tiles.map((t) => `<div class="tile"><div class="k">${esc(t.k)}</div><div class="v">${t.v}</div><div class="s">${esc(t.s)}</div></div>`).join("")}</div>` : ""}
    ${notes.map((n) => `<p class="note">${n}</p>`).join("")}
    <details><summary>Every quarter from NEA</summary><div class="tablewrap"><table><thead><tr><th class="l">Quarter</th><th>Interruptions</th><th>Minutes</th><th>Hours</th><th class="l">NEA's flags</th><th class="l">Source</th></tr></thead><tbody>${
      [...rows].reverse().map((r) => `<tr><td class="l">${quarterLabel(r.period)}</td><td>${r.saifi == null ? "–" : r.saifi.toFixed(2)}</td><td>${r.saidi_minutes == null ? "–" : r.saidi_minutes.toLocaleString("en-PH")}</td><td>${hours(r.saidi_minutes)}</td>
        <td class="l">${r.missing ? esc(r.missing) : `${r.saifi_status} / ${r.saidi_status}`}</td><td class="l"><a href="${esc(rel.source_urls[r.period])}" rel="noopener">report, p. ${r.page}</a></td></tr>`).join("")}</tbody></table></div>
      <p class="note">Flags: C meets NEA's standard, NC doesn't (NEA also gives NC when no data was sent). Figures as printed; the yearly standard applies to the full year.</p></details>`;
}

function medianSeries() {
  if (state.klass === "generation") return null; // no published medians for the generation charge
  const u = current.u, peers = utilities.filter((x) => x.region === u.region).length;
  return { peers, region: peers >= MIN_PEERS ? medians.regions[u.region]?.[state.klass] || {} : null, national: medians.national[state.klass] };
}

// ---------- Chart ----------

function renderChart() {
  const { u, rates } = current, klass = state.klass;
  const own = rates.series[klass], med = medianSeries();
  const ms = [...new Set([...own.map((d) => d[0]), ...(med ? Object.keys(med.national) : [])])].sort();
  const series = [{ name: u.doe_name, color: "var(--series-1)", pts: own }];
  if (med?.region) series.push({ name: `${u.region} median`, color: "var(--series-2)", pts: Object.entries(med.region) });
  if (med) series.push({ name: "National median", color: "var(--series-3)", pts: Object.entries(med.national) });
  $("legend").innerHTML = series.map((s) => `<span><i class="sw" style="background:${s.color}"></i>${esc(s.name)}</span>`).join("");
  $("trend-title").textContent = `${CLASSES[klass]}, PHP per kWh`;
  lineChart(series, ms);

  const nda = klass === "generation" ? null : rates.nda_months[klass];
  const flagged = rates.flagged_by_qa.filter((f) => f.class === klass);
  $("gaps").innerHTML = [
    own.length ? `${own.length} months with a value.` : `DOE has no ${CLASSES[klass].toLowerCase()} figures for this utility.`,
    nda ? `${nda} ${nda === 1 ? "month" : "months"} marked NDA (no data available) by DOE.` : "",
    flagged.length ? `Left out as implausible: ${flagged.map((f) => `${monthLabel(f.month)} (${f.value})`).join(", ")}.` : "",
    klass === "generation" ? "Generation charge is shown for the utility alone." : "",
    med && !med.region ? `No regional median: ${med.peers === 1 ? `${u.doe_name} is the only utility` : `only ${med.peers} utilities are`} in ${u.region}.` : "",
  ].filter(Boolean).join(" ");

  const lookup = series.map((s) => new Map(s.pts));
  $("table").innerHTML = `<table><thead><tr><th>Month</th>${series.map((s) => `<th>${esc(s.name)}</th>`).join("")}</tr></thead><tbody>${
    [...ms].reverse().map((m) => `<tr><td>${monthLabel(m)}</td>${lookup.map((l) => `<td>${fmt(l.get(m))}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
}

function lineChart(series, ms) {
  const box = $("chart");
  box.querySelector("svg")?.remove();
  const all = series.flatMap((s) => s.pts.map((p) => p[1]));
  if (!all.length || !ms.length) return;
  // draw at the container's own width so axis text stays 11px on phones
  const W = Math.max(box.clientWidth, 300), H = W < 500 ? 240 : 320, m = { t: 12, r: 8, b: 28, l: 32 };
  const lo = Math.floor(Math.min(...all) - 0.5), hi = Math.ceil(Math.max(...all) + 0.5);
  const x = (mo) => m.l + (ms.indexOf(mo) / Math.max(ms.length - 1, 1)) * (W - m.l - m.r);
  const y = (v) => H - m.b - ((v - lo) / (hi - lo)) * (H - m.t - m.b);
  const step = Math.max(1, Math.ceil((hi - lo) / 6));
  let g = "";
  for (let v = Math.ceil(lo / step) * step; v <= hi; v += step)
    g += `<line x1="${m.l}" x2="${W - m.r}" y1="${y(v)}" y2="${y(v)}" stroke="var(--grid)"/><text x="${m.l - 6}" y="${y(v) + 4}" text-anchor="end" font-size="11" fill="var(--ink-faint)">${v}</text>`;
  const yearStep = W < 500 ? 2 : 1;
  for (const mo of ms.filter((mo) => mo.endsWith("-01") && +mo.slice(0, 4) % yearStep === 0))
    g += `<text x="${x(mo)}" y="${H - 8}" text-anchor="middle" font-size="11" fill="var(--ink-faint)">${mo.slice(0, 4)}</text>`;
  // break the line where a month is missing instead of bridging the gap
  const path = (pts) => {
    const have = new Map(pts);
    let d = "", pen = false;
    for (const mo of ms) {
      if (!have.has(mo)) { pen = false; continue; }
      d += `${pen ? "L" : "M"}${x(mo).toFixed(1)},${y(have.get(mo)).toFixed(1)}`;
      pen = true;
    }
    return d;
  };
  const lines = [...series].reverse().map((s) => `<path d="${path(s.pts)}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>`).join("");
  box.insertAdjacentHTML("afterbegin", `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(series.map((s) => s.name).join(", "))} by month">${g}
    <line x1="${m.l}" x2="${W - m.r}" y1="${H - m.b}" y2="${H - m.b}" stroke="var(--rule)"/>${lines}
    <line id="xh" y1="${m.t}" y2="${H - m.b}" stroke="var(--ink-faint)" stroke-dasharray="3 3" visibility="hidden"/>
    <g id="dots"></g><rect x="${m.l}" y="0" width="${W - m.l - m.r}" height="${H}" fill="transparent" id="hit"/></svg>`);

  const maps = series.map((s) => new Map(s.pts));
  const svgEl = box.querySelector("svg"), tip = $("tip"), xh = svgEl.querySelector("#xh");
  svgEl.querySelector("#hit").addEventListener("pointermove", (e) => {
    const pt = svgEl.createSVGPoint();
    pt.x = e.clientX; pt.y = e.clientY;
    const p = pt.matrixTransform(svgEl.getScreenCTM().inverse());
    const mo = ms[Math.max(0, Math.min(ms.length - 1, Math.round(((p.x - m.l) / (W - m.l - m.r)) * (ms.length - 1))))];
    xh.setAttribute("x1", x(mo)); xh.setAttribute("x2", x(mo)); xh.setAttribute("visibility", "visible");
    svgEl.querySelector("#dots").innerHTML = series.map((s, j) => maps[j].has(mo)
      ? `<circle cx="${x(mo)}" cy="${y(maps[j].get(mo))}" r="4.5" fill="${s.color}" stroke="var(--paper-raised)" stroke-width="2"/>` : "").join("");
    tip.innerHTML = `<b>${monthLabel(mo)}</b>` + series.map((s, j) => `<div class="row"><span><i class="sw" style="background:${s.color}"></i> ${esc(s.name)}</span><span>${fmt(maps[j].get(mo))}</span></div>`).join("");
    place(tip, box, e);
  });
  svgEl.querySelector("#hit").addEventListener("pointerleave", () => {
    tip.style.display = "none"; xh.setAttribute("visibility", "hidden"); svgEl.querySelector("#dots").innerHTML = "";
  });
}

function place(tip, box, e) {
  tip.style.display = "block";
  const bx = box.getBoundingClientRect(), left = e.clientX - bx.left, top = e.clientY - bx.top;
  tip.style.left = `${Math.min(Math.max(left + 12, 0), bx.width - tip.offsetWidth)}px`;
  tip.style.top = `${top - tip.offsetHeight - 12 < 0 ? top + 16 : top - tip.offsetHeight - 12}px`;
}

// ---------- Map ----------

const saidiMode = () => state.measure === "saidi";
const bins = () => (saidiMode() ? BINS.saidi : state.klass === "generation" ? BINS.generation : BINS.retail);
const binOf = (v) => bins().findIndex((t) => v < t) + 1 || bins().length + 1; // 1..5

function rateOf(uid, mi) { return panel.series[state.klass][uid]?.[mi] ?? null; }
// what the map shows for one utility: this month's rate, or last full year's hours without power
const valueOf = (uid, mi) => (saidiMode() ? saidiHours(uid) : rateOf(uid, mi));
const showValue = (uid, v) => (v != null ? (saidiMode() ? `${v.toFixed(1)} h` : fmt(v))
  : saidiMode() && byId.get(uid)?.du_type !== "EC" ? "not published" : "no figure");

// The map: MapLibre (self-hosted under vendor/) over an OpenFreeMap basemap (OpenStreetMap data, no key, no cookies).
// The basemap is the site's only third-party request, and it can be switched off (#...&base=0).
const MAPLIBRE = "./vendor/maplibre-gl-6.16.0/maplibre-gl.mjs";
const BASEMAP = { light: "https://tiles.openfreemap.org/styles/positron", dark: "https://tiles.openfreemap.org/styles/dark" };
const PH_BOUNDS = [[116.9, 4.4], [126.7, 21.2]];
const dark = matchMedia("(prefers-color-scheme: dark)");
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");
let map, ml, shownBase;
const blankStyle = () => ({ version: 8, sources: {}, layers: [{ id: "bg", type: "background", paint: { "background-color": cssVar("--paper-tint") } }] });

const cssVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const theme = () => (document.documentElement.dataset.theme || (dark.matches ? "dark" : "light"));

function addLayers() {
  const before = map.getStyle().layers.find((l) => l.type === "symbol")?.id; // keep place names above the colours
  if (!map.getSource("munis")) map.addSource("munis", { type: "geojson", data: geo.url, promoteId: "c" });
  const seq = [1, 2, 3, 4, 5].flatMap((k) => [k, cssVar(`--seq-${k}`)]);
  map.addLayer({ id: "munis-fill", type: "fill", source: "munis", paint: {
    "fill-color": ["match", ["coalesce", ["feature-state", "k"], 0], ...seq, 6, cssVar("--nodata"), "rgba(0,0,0,0)"],
    "fill-opacity": 0.8 } }, before);
  map.addLayer({ id: "munis-line", type: "line", source: "munis", paint: { "line-color": cssVar("--paper-raised"), "line-width": 0.4, "line-opacity": 0.8 } }, before);
  map.addLayer({ id: "munis-none", type: "line", source: "munis", filter: ["==", ["get", "u"], "[]"],
    paint: { "line-color": cssVar("--ink-faint"), "line-width": 0.8, "line-dasharray": [2, 2] } }, before);
  map.addLayer({ id: "sel", type: "line", source: "munis", filter: ["in", ["get", "c"], ["literal", []]],
    paint: { "line-color": cssVar("--ink"), "line-width": 1.6 } });
  map.addLayer({ id: "hov", type: "line", source: "munis", filter: ["==", ["get", "c"], ""], paint: { "line-color": cssVar("--ink"), "line-width": 1 } });
  map.addLayer({ id: "pick", type: "line", source: "munis", filter: ["==", ["get", "c"], ""], paint: { "line-color": cssVar("--ink"), "line-width": 3 } });
}

async function buildMap() {
  ml = await import(MAPLIBRE);
  const raw = await json("geo/municipalities.geojson");
  geo = { url: "geo/municipalities.geojson",
          features: raw.features.map(({ properties: p }) => ({ c: p.c, n: p.n, p: p.p, u: JSON.parse(p.u), b: p.b })) };
  geo.byCode = new Map(geo.features.map((f) => [f.c, f]));
  shownBase = state.base;
  map = new ml.Map({
    container: "map", style: state.base ? BASEMAP[theme()] : blankStyle(), bounds: PH_BOUNDS, fitBoundsOptions: { padding: 12 },
    attributionControl: { compact: true }, dragRotate: false, pitchWithRotate: false, touchPitch: false,
    cooperativeGestures: matchMedia("(pointer: coarse)").matches, // on phones, one finger still scrolls the page
  });
  map.addControl(new ml.NavigationControl({ showCompass: false }), "top-right");
  map.addControl(new ml.FullscreenControl(), "top-right");
  map.on("style.load", () => { addLayers(); paintMap(); });

  const tip = $("maptip");
  map.on("mousemove", "munis-fill", (e) => {
    const f = geo.byCode.get(e.features[0].properties.c);
    map.getCanvas().style.cursor = "pointer";
    map.setFilter("hov", ["==", ["get", "c"], f.c]);
    const mi = panel.months.indexOf(state.month);
    const rows = f.u.length ? f.u.map(([id, q]) => {
      const v = valueOf(id, mi);
      return `<div class="row"><span>${esc(byId.get(id)?.doe_name || id)}</span><span>${showValue(id, v)}</span></div>${q ? `<div class="q">${esc(q)}</div>` : ""}`;
    }).join("") : `<div class="q">No utility listed in the coverage source</div>`;
    tip.innerHTML = `<b>${esc(f.n)}</b><div class="q">${esc(f.p || "independent city")}</div>${rows}`;
    place(tip, $("map"), e.originalEvent);
  });
  map.on("mouseleave", "munis-fill", () => { map.getCanvas().style.cursor = ""; tip.style.display = "none"; map.setFilter("hov", ["==", ["get", "c"], ""]); });
  map.on("click", "munis-fill", (e) => go({ place: e.features[0].properties.c }));
  $("basemap").addEventListener("change", () => go({ base: $("basemap").checked }));
  dark.addEventListener("change", () => state.base && map.setStyle(BASEMAP[theme()]));
  addEventListener("keydown", (e) => { if (e.key === "Escape" && state.place && state.view === "map") go({ place: null }); });
  // no waiting for the first paint: the legend, counts and place panel don't depend on it; colours apply on style.load
}

// Colour each place by its utility's rate this month (feature state, so a month change repaints without reloading shapes)
function paintMap() {
  const mi = panel.months.indexOf(state.month);
  const counts = { colored: 0, nodata: 0, none: 0 };
  const ready = !!map?.getLayer("munis-fill");
  for (const f of geo.features) {
    let k;
    if (!f.u.length) { k = 0; counts.none++; }
    else {
      const v = median(f.u.map(([id]) => valueOf(id, mi)).filter((x) => x != null));
      if (v == null) { k = 6; counts.nodata++; } else { k = binOf(v); counts.colored++; }
    }
    if (ready) map.setFeatureState({ source: "munis", id: f.c }, { k });
  }
  if (!ready) return counts;
  map.setFilter("sel", ["in", ["get", "c"], ["literal", geo.features.filter((f) => f.u.some(([id]) => id === state.du)).map((f) => f.c)]]);
  map.setFilter("pick", ["==", ["get", "c"], state.place || ""]);
  return counts;
}

async function renderMap() {
  if (!map) {
    $("map").insertAdjacentHTML("afterbegin", `<p class="note" id="maploading">Loading the map…</p>`);
    await buildMap();
    $("maploading")?.remove();
  }
  // switching the basemap swaps the style; our layers come back on style.load
  if (shownBase !== state.base) {
    shownBase = state.base;
    map.setStyle(state.base ? BASEMAP[theme()] : blankStyle());
  }
  $("basemap").checked = state.base;
  map.resize();
  for (const b of $("measures").children) b.setAttribute("aria-pressed", b.dataset.m === state.measure);
  $("map-title").textContent = saidiMode() ? `Hours without power per customer, ${rel.full_year}, by municipality`
    : `${CLASSES[state.klass]} by municipality, ${monthLabel(state.month)}`;
  $("ramptitle").textContent = saidiMode() ? `Hours without power per customer in ${rel.full_year} (NEA's SAIDI)` : "PHP per kWh";
  $("nodatakey").textContent = saidiMode() ? "No NEA figure: private utility (not published) or none sent" : "Utility has no figure this month (NDA)";
  const counts = paintMap();
  $("selkey").textContent = `${current.u.doe_name}'s area`;

  const b = bins();
  $("ramp").innerHTML = [1, 2, 3, 4, 5].map((k) => `<i style="background:var(--seq-${k})"></i>`).join("");
  $("rampl").innerHTML = [`< ${b[0]}`, `${b[0]}–${b[1]}`, `${b[1]}–${b[2]}`, `${b[2]}–${b[3]}`, `${b[3]}+`].map((t) => `<span>${t}</span>`).join("");
  const n = (x) => x.toLocaleString("en-PH");
  $("mapnote").textContent = saidiMode()
    ? `${n(counts.colored)} places with an NEA figure for ${rel.full_year}, ${n(counts.nodata)} served only by a private utility or a cooperative that sent none, ${n(counts.none)} with no utility listed. NEA's figure is the cooperative's average across its whole area, so every place it serves takes the same colour.`
    : `${n(counts.colored)} places with a figure, ${n(counts.nodata)} whose utility has none this month, ${n(counts.none)} with no utility listed.`;
  renderPlace();
}

// The clicked place: who serves it, at what rate, and how that compares; the map flies to it.
let shownPlace = null;
function renderPlace() {
  const card = $("placecard");
  const f = state.place && geo.byCode.get(state.place);
  const fly = (bounds, maxZoom) => map.fitBounds(bounds, { padding: 48, maxZoom, duration: reduceMotion.matches ? 0 : 900 });
  if (!f) {
    card.hidden = true;
    if (shownPlace) fly(PH_BOUNDS, 6);
    shownPlace = null;
    return;
  }
  if (shownPlace !== f.c) fly([[f.b[0], f.b[1]], [f.b[2], f.b[3]]], 11);
  shownPlace = f.c;
  const mi = panel.months.indexOf(state.month);
  const nat = saidiMode() ? rel.medians[rel.full_year_period].saidi_minutes / 60
    : median(Object.values(panel.series[state.klass]).map((s) => s[mi]).filter((v) => v != null));
  const spark = (uid) => {
    const s = panel.series[state.klass][uid].slice(-36);
    const vals = s.filter((v) => v != null);
    if (vals.length < 2) return "";
    const lo = Math.min(...vals), hi = Math.max(...vals), W = 120, H = 28;
    let d = "", pen = false;
    s.forEach((v, k) => {
      if (v == null) { pen = false; return; }
      d += `${pen ? "L" : "M"}${(k / (s.length - 1) * W).toFixed(1)},${(H - 2 - (v - lo) / (hi - lo || 1) * (H - 4)).toFixed(1)}`;
      pen = true;
    });
    return `<svg class="spark" viewBox="0 0 ${W} ${H}" aria-hidden="true"><path d="${d}" fill="none" stroke="var(--series-1)" stroke-width="1.5"/></svg>`;
  };
  card.hidden = false;
  card.innerHTML = `<div class="pc-head"><div><h3>${esc(f.n)}</h3>
      <p class="note">${esc(f.p || "Independent city")} · PSGC <code>${f.c}</code></p></div>
      <button type="button" class="pc-close" aria-label="Close and show the whole country">✕</button></div>
    ${f.u.length ? f.u.map(([id, q]) => {
      const u = byId.get(id), v = valueOf(id, mi);
      return `<div class="pc-du">
        <div class="pc-name"><strong>${esc(u.doe_name)}</strong> <span class="note">${esc(u.name || "")}</span></div>
        <div class="pc-figs"><div><span class="pc-v">${v == null ? "–" : saidiMode() ? v.toFixed(1) : fmt(v)}</span> <span class="note">${saidiMode()
            ? `hours without power per customer, ${rel.full_year}` : `PHP/kWh, ${CLASSES[state.klass].toLowerCase()}, ${monthLabel(state.month)}`}</span>
          ${v != null && nat ? `<div class="note">${v >= nat ? "+" : "−"}${Math.abs((v - nat) / nat * 100).toFixed(0)}% vs. ${saidiMode() ? "cooperative" : "national"} median (${saidiMode() ? `${nat.toFixed(1)} h` : fmt(nat)})</div>`
            : `<div class="note">${saidiMode() ? (u.du_type === "EC" ? "No NEA figure for this year" : "Private utility: ERC doesn't publish this") : "DOE has no figure this month"}</div>`}</div>
          ${saidiMode() ? "" : spark(id)}</div>
        ${q ? `<p class="note">Coverage note: ${esc(q)}</p>` : ""}
        <div class="pc-actions"><button type="button" class="btn ghost" data-trend="${id}">See trend</button>
          <button type="button" class="btn ghost" data-area="${id}">Outline its area</button></div></div>`;
    }).join("") : `<p class="note">No utility is listed for this place in the coverage source.</p>`}`;
  card.querySelector(".pc-close").onclick = () => go({ place: null });
  card.onclick = (e) => {
    if (e.target.dataset.trend) go({ du: e.target.dataset.trend, view: "chart" });
    if (e.target.dataset.area) go({ du: e.target.dataset.area });
  };
}

// ---------- Table (every utility, one month) ----------

function renderTable() {
  const mi = panel.months.indexOf(state.month);
  const rows = utilities.map((u) => ({ u, v: rateOf(u.utility_id, mi) }));
  const nat = median(rows.map((r) => r.v).filter((v) => v != null));
  const max = Math.max(...rows.map((r) => r.v ?? 0));
  rows.sort((a, b) => (b.v ?? -1) - (a.v ?? -1) || a.u.doe_name.localeCompare(b.u.doe_name));
  const have = rows.filter((r) => r.v != null).length;
  $("all-title").textContent = `${CLASSES[state.klass]}, ${monthLabel(state.month)}`;
  $("allnote").innerHTML = `${have} of ${rows.length} utilities have a figure; national median ${fmt(nat)} PHP/kWh. Select a name to see its trend. Hours without power per customer are NEA's full-year figures for electric cooperatives; ERC publishes none for private utilities.`;
  $("alltable").innerHTML = `<table><thead><tr><th class="l">Utility</th><th class="l">Region</th><th class="l">Type</th><th>PHP/kWh</th><th>vs. median</th><th>Hours without power, ${rel.full_year}</th></tr></thead><tbody>${
    rows.map(({ u, v }) => `<tr${u.utility_id === state.du ? ' style="background:var(--paper-tint)"' : ""}>
      <td><button class="link" data-id="${u.utility_id}">${esc(u.doe_name)}</button></td><td class="l">${esc(u.region)}</td><td class="l">${u.du_type}</td>
      <td>${v == null ? '<span class="note">no figure</span>' : `<i class="bar" style="width:${(v / max) * 60}px"></i>${fmt(v)}`}</td>
      <td>${v == null || !nat ? "" : `${v >= nat ? "+" : "−"}${Math.abs(((v - nat) / nat) * 100).toFixed(0)}%`}</td>
      <td>${u.du_type !== "EC" ? '<span class="note">not published</span>' : saidiHours(u.utility_id) == null ? '<span class="note">no figure</span>' : saidiHours(u.utility_id).toFixed(1)}</td></tr>`).join("")}</tbody></table>`;
  $("alltable").onclick = (e) => e.target.dataset?.id && go({ du: e.target.dataset.id, view: "chart" });
}


init().catch((e) => { $("meta").textContent = `Could not load the data: ${e.message}`; });
