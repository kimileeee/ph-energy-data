"""Render site/methodology.html from pipeline/templates/methodology.html.

Same principle as the Naga budget portal's methodology page: prose alone rots, so every figure on the page
is computed here from the build's own outputs, and the sentences around them say only things that stay
true when the data changes. An unknown {{placeholder}} fails the build.
"""

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from pipeline.doe import PLAUSIBLE
from pipeline.fetch import GEO_TAG, PSGC_TAG, RAW, SHEET_URL
from pipeline.verify import LOOKBACK, TOLERANCE

DATA = RAW.parent
ROOT = DATA.parent
TEMPLATES = Path(__file__).parent / "templates"
PAGES = ["methodology.html", "proposal.html"]  # each rendered from templates/<name> to site/<name>
WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine"}


def n(x) -> str:
    return f"{x:,}"


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def month(m: str) -> str:
    return datetime.strptime(m, "%Y-%m").strftime("%b %Y")


def runs(months: list[str]) -> str:
    """Consecutive months as readable runs: "Jan 2018 to Dec 2019, Jul 2025 and Oct 2026"."""
    idx = lambda m: int(m[:4]) * 12 + int(m[5:]) - 1
    out, start, prev = [], None, None
    for m in sorted(months):
        if prev is not None and idx(m) == idx(prev) + 1:
            prev = m
            continue
        if start:
            out.append(month(start) if start == prev else f"{month(start)} to {month(prev)}")
        start = prev = m
    if start:
        out.append(month(start) if start == prev else f"{month(start)} to {month(prev)}")
    return out[0] if len(out) == 1 else ", ".join(out[:-1]) + " and " + out[-1]


def values() -> dict[str, str]:
    s = json.loads((DATA / "stats.json").read_text())
    manifest = json.loads((RAW / "manifest.json").read_text())
    rates = pd.read_csv(DATA / "rates.csv", keep_default_na=False)
    utilities = pd.read_csv(DATA / "utilities.csv", dtype=str, keep_default_na=False)
    areas = pd.read_csv(DATA / "utility_areas.csv", dtype=str, keep_default_na=False)
    disc = pd.read_csv(DATA / "qa" / "discrepancies.csv", dtype=str, keep_default_na=False)
    issues = pd.read_csv(DATA / "qa" / "doe_sheet_issues.csv", dtype=str)
    geo = json.loads((ROOT / "site" / "geo" / "municipalities.geojson").read_text())

    res = rates[(rates.customer_class == "residential") & (rates.rate_note != "blank")].merge(utilities[["utility_id", "grid"]])
    res["nda"] = res.rate_note == "NDA"
    by_year = res.groupby(res.month.str[:4]).nda.mean()
    by_grid = res.groupby("grid").nda.mean()
    checked = disc[~disc.status.str.startswith("doe_missing")]
    per = {}
    for uid in ("meralco", "celcor", "tei", "aec", "cedc"):
        c = checked[checked.utility_id == uid]
        if c.empty:
            per |= {f"{uid}_{k}": "0" for k in ("n", "matches")} | {f"{uid}_{k}": "–" for k in ("from", "to")} | \
                   {f"{uid}_example_url": "#", f"{uid}_example_label": "no document read this run"}
            continue
        latest = c.sort_values("month").iloc[-1]
        per |= {f"{uid}_n": n(len(c)), f"{uid}_matches": n(int((c.status == "match").sum())),
                f"{uid}_from": month(c.month.min()), f"{uid}_to": month(c.month.max()),
                f"{uid}_example_url": latest.source_url,
                f"{uid}_example_label": f"the {month(latest.month)} {'image' if latest.get('read_by') == 'image' else 'PDF'}"}
    # documents read by the image reader in this run's checks (the cache can hold older reads)
    read_docs = set(disc[disc.read_by == "image"].sha256) if "read_by" in disc else set()
    extracted = [x for p in (RAW / "sources" / "_extract").glob("*.json") if (x := json.loads(p.read_text()))["sha256"] in read_docs]
    xfacts = [f for x in extracted for pg in x["pages"] for f in pg["facts"]]
    per |= {"img_docs": n(len(extracted)), "img_facts": n(len(xfacts)),
            "img_grounded": n(sum(f["grounded"] for f in xfacts))}
    cres = checked[(checked.utility_id == "celcor") & (checked.measure == "residential")].copy()
    cres["gap"] = cres.doe_value.astype(float) - cres.source_value.astype(float)
    # AEC: DOE's month-on-month change vs the change AEC's own notice states; where they part, how far DOE's level drifts
    a = checked[(checked.utility_id == "aec") & (checked.measure == "residential")].sort_values("month")
    a_bad = a[a.status == "mismatch"]
    if len(a_bad):
        first = a_bad.iloc[0]
        rates_aec = rates[(rates.utility_id == "aec") & (rates.customer_class == "residential")].set_index("month").rate_php_per_kwh
        start = a[a.month < first.month].month.max()  # last month where the two agree
        level, drift = float(rates_aec[start]), []
        for r in a[a.month > start].itertuples():
            level += float(r.source_value)
            if rates_aec.get(r.month, "") != "":
                drift.append((r.month, float(rates_aec[r.month]) - level))
        aec_sentence = (f"AEC's monthly notice states the change from the previous month, so it is compared with the change in DOE's figures. "
                        f"They agree in {int((a.status == 'match').sum())} of {len(a)} months. In {month(first.month)}, DOE's figure moves "
                        f"{float(first.doe_value):+.2f} PHP/kWh while AEC's notice states {float(first.source_value):+.4f}; AEC's components "
                        f"for that month add up to its stated change, and two of its notices print the same figure. Following AEC's own "
                        f"changes from {month(start)}, when both agree, DOE's AEC figure sits "
                        f"{min(d for _, d in drift):.2f} to {max(d for _, d in drift):.2f} PHP/kWh higher through {month(drift[-1][0])}. "
                        f"AEC's generation-charge changes match DOE's in every month checked.")
    else:
        aec_sentence = "AEC's stated changes match the change in DOE's figures in every month checked."
    fl = checked[(checked.utility_id == "cedc") & (checked.check_type == "floor")].copy()
    fl["gap"] = fl.source_value.astype(float) - fl.doe_value.astype(float)
    below = fl[fl.status == "mismatch"]
    cg = checked[(checked.utility_id == "cedc") & (checked.measure == "generation")]
    cedc_sentence = (f"CEDC publishes a rate schedule, not a bill, so the <a href=\"#formula\">formula</a> gives its residential charges "
                     f"at 200 kWh before VAT and local taxes; an all-in rate can't be lower. DOE's figure is lower in {len(below)} of {len(fl)} "
                     f"months (by up to {below.gap.max():.2f} PHP/kWh). In {int(fl.explain.str.contains('understated').sum())} of those "
                     f"{len(fl)} months the reader missed some charges that it found in other months (usually the universal charges), "
                     f"which only lowers the floor there, so any gap in those months is understated. CEDC's generation charge matches DOE in "
                     f"{int((cg.status == 'match').sum())} of {len(cg)} months, and fills a month DOE marks NDA."
                     if len(below) else "DOE's figure clears CEDC's own charges before tax in every month checked.")
    per |= {"aec_sentence": aec_sentence, "cedc_sentence": cedc_sentence}

    # Meralco: which consumption level DOE's figure corresponds to, by period
    mr = checked[(checked.utility_id == "meralco") & (checked.measure == "residential")].sort_values("month")
    ok = mr[mr.status == "match"]
    bad = mr[mr.status == "mismatch"]
    lvl = bad.explain.str.extract(r"matches the (\d+) kWh bill")[0].dropna()
    top = lvl.value_counts()
    per |= {"meralco_200_from": month(ok.month.min()) if len(ok) else "–", "meralco_200_to": month(ok.month.max()) if len(ok) else "–",
            "meralco_level": top.index[0] if len(top) else "another",
            "meralco_level_n": f"{int(top.iloc[0])} of the {len(bad)}" if len(top) else "none of the",
            "meralco_sentence": (f"Meralco's archive goes back to 2009, so the check also covered the older months where DOE has no "
                                 f"generation charge and does have a residential figure. In {'all' if len(ok[ok.month < '2025-01']) == 0 else len(bad)} "
                                 f"{len(bad)} of them ({runs(list(bad.month))}), DOE's figure differs from Meralco's 200 kWh bill. In {len(lvl)} of those it equals another row of Meralco's own "
                                 f"table exactly: the {top.index[0]} kWh bill in {int(top.iloc[0])} months"
                                 + (f", and other levels in {len(lvl) - int(top.iloc[0])}" if len(lvl) > int(top.iloc[0]) else "")
                                 + ". So DOE's figures are copied correctly, but at a different consumption level than it uses today, which a "
                                 "series spanning both periods needs to say." if len(bad) else
                                 "DOE's Meralco figures match the 200 kWh bill in every month checked.")}

    # cells DOE leaves empty that a utility's own document fills, grouped into runs of months for the proposal
    fills = disc[disc.status.str.startswith("doe_missing") & (disc.check_type == "same")]
    label = {"meralco": "Meralco", "cedc": "Clark Electric", "celcor": "CELCOR", "tei": "Tarlac Electric", "aec": "Angeles Electric"}
    what = {"generation": "generation charge", "residential": "residential rate"}
    parts = []
    for (u, m), g in fills.groupby(["utility_id", "measure"]):
        ms = sorted(g.month)
        kind = "images" if (g.read_by == "image").any() else "PDFs"
        count = f"{len(ms)} months" if len(ms) > 1 else "one month"
        parts.append((len(ms), f"{label.get(u, u)}'s {what[m]} for {count} ({runs(ms)}, from its {kind})"))
    parts = [t for _, t in sorted(parts, reverse=True)]
    per |= {"fill_sentence": (f"Reading the utilities' own archives back to 2018 gave {len(fills)} figures DOE's sheet doesn't have: "
                              + "; ".join(parts) + "." if parts else "No empty cell could be filled from the documents read so far."),
            "gen_matches": n(int(((checked.measure == "generation") & (checked.status == "match")).sum())),
            "diff_list": "; ".join(f"{label.get(u, u)} ({k})" for (u, mm), k in
                                   checked[checked.status == "mismatch"].groupby(["utility_id", "measure"]).size().sort_values(ascending=False).items()),
            "filled_cells": n(len(fills))}
    cgen = checked[(checked.utility_id == "celcor") & (checked.measure == "generation")]
    mism = checked[checked.status == "mismatch"]
    groups = mism.groupby(["utility_id", "measure"]).size()
    names = {"celcor": "CELCOR", "aec": "Angeles Electric", "cedc": "Clark Electric", "meralco": "Meralco", "tei": "Tarlac Electric"}
    diff_sentence = ("No figure differs." if mism.empty else
                     f"{len(mism)} comparisons differ: " +
                     ", ".join(f"{names.get(u, u)} {m} ({k})" for (u, m), k in groups.items()) + "." +
                     (" Every generation-charge comparison matches." if not (mism.measure == "generation").any() else ""))
    feats = [{**f["properties"], "u": json.loads(f["properties"]["u"])} for f in geo["features"]]
    wiki_rev = manifest.get("wikipedia/du_list.wiki", {}).get("revision", {})
    lead = issues.check.value_counts()

    return {
        "built": s["built_at"][:10],
        "sheet_url": SHEET_URL,
        "utilities": n(s["utilities"]), "piou": n(s["utilities_by_type"]["PIOU"]), "ec": n(s["utilities_by_type"]["EC"]),
        "months": n(s["months_compiled"]), "first_month": month(s["first_month"]), "latest_month": month(s["latest_month"]),
        "empty_months": ", ".join(month(m) for m in s["months_in_sheet_not_compiled"]) or "none",
        "rate_cells": n(s["rate_cells"]), "rate_values": n(s["rate_cells_with_value"]),
        "nda_share": pct(s["nda_share"]), "nda_res": pct(s["nda_share_by_class"]["residential"]),
        "nda_com": pct(s["nda_share_by_class"]["commercial"]), "nda_ind": pct(s["nda_share_by_class"]["industrial"]),
        "nda_gen": pct(s["generation_nda_share"]),
        "nda_first_year": by_year.index[0], "nda_first_year_share": pct(by_year.iloc[0]),
        "nda_last_year": by_year.index[-1], "nda_last_year_share": pct(by_year.iloc[-1]),
        "nda_min_year": by_year.idxmin(), "nda_min_share": pct(by_year.min()),
        "nda_offgrid": pct(by_grid.get("OFFGRID", 0)), "nda_ongrid": pct(by_grid.get("ONGRID", 0)),
        "latest_cov": pct(s["latest_month_residential_coverage"]),
        "plaus_lo": f"{PLAUSIBLE[0]:g}", "plaus_hi": f"{PLAUSIBLE[1]:g}",
        "issues_total": n(len(issues)),
        "issues_outside": n(int(lead.filter(like="outside").sum())),
        "issues_tabs": n(int(lead.get("attribute differs across tabs", 0))),
        "issues_unparsed": n(int(lead.get("unparseable cell", 0))),
        "with_prov": n(s["utilities_with_province_psgc"]),
        "without_prov": n(s["utilities"] - s["utilities_with_province_psgc"]),
        "areas_listed": n(s["areas_listed"]), "areas_matched": n(s["areas_matched_to_psgc"]),
        "areas_unmatched": n(s["areas_listed"] - s["areas_matched_to_psgc"]),
        "areas_partial": n(int((areas.qualifier != "").sum())),
        "wiki_rev": str(wiki_rev.get("revid", "")), "wiki_date": wiki_rev.get("timestamp", "")[:10],
        "psgc_tag": PSGC_TAG, "geo_tag": GEO_TAG,
        "checks": n(len(checked)), "matches": n(int((checked.status == "match").sum())),
        "mismatches": n(int((checked.status == "mismatch").sum())),
        "doe_blank": n(int(disc.status.str.startswith("doe_missing").sum())),
        "tolerance": f"{TOLERANCE:g}", "lookback": n(LOOKBACK),
        **per, "diff_sentence": diff_sentence,
        "n_checked": WORDS.get(checked.utility_id.nunique(), str(checked.utility_id.nunique())),
        "gen_checks": n(int((checked.measure == "generation").sum())),
        "celcor_res_n": n(len(cres)), "celcor_below": n(int((cres.gap < 0).sum())), "celcor_above": n(int((cres.gap > 0).sum())),
        "celcor_max_below": f"{-cres.gap.min():.2f}" if (cres.gap < 0).any() else "0",
        "celcor_res_mismatch": n(int((cres.status == "mismatch").sum())),
        "celcor_nolevel": n(int(cres.explain.str.startswith("No consumption level").sum())),
        "celcor_gen_n": n(len(cgen)), "celcor_gen_matches": n(int((cgen.status == "match").sum())),
        "links_web": n(s["doe_source_links"].get("website", 0)), "links_fb": n(s["doe_source_links"].get("facebook", 0)),
        "links_none": n(s["doe_source_links"].get("none", 0)), "links_bad": n(s["doe_source_links_not_ok"]),
        "map_drawn": n(len(feats)), "map_served": n(sum(1 for f in feats if f["u"])),
        "map_none": n(sum(1 for f in feats if not f["u"])),
        "map_shared": n(sum(1 for f in feats if len(f["u"]) > 1)),

    }


def bust_cache() -> None:
    """Stamp each page's local .js/.css links with a content hash, so a redeploy never runs with a stale script."""
    site = ROOT / "site"
    digest = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()[:10] for p in [*site.glob("*.js"), *site.glob("*.css")]}
    pat = re.compile(r'((?:src|href)=")([\w-]+\.(?:js|css))(?:\?v=[0-9a-f]+)?(")')
    for page in site.glob("*.html"):
        html = page.read_text()
        new = pat.sub(lambda m: f"{m[1]}{m[2]}?v={digest[m[2]]}{m[3]}" if m[2] in digest else m[0], html)
        if new != html:
            page.write_text(new)


def main() -> None:
    v = values()
    for name in PAGES:
        html = re.sub(r"\{\{(\w+)\}\}", lambda m: v[m.group(1)], (TEMPLATES / name).read_text())
        (ROOT / "site" / name).write_text(html)
        print(f"wrote site/{name}")
    bust_cache()


if __name__ == "__main__":
    main()
