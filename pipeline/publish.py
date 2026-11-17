"""Publish: Parquet copies, Frictionless datapackage.json, static JSON API, coverage stats.

Outputs: data/*.parquet, data/datapackage.json, data/stats.json,
         site/api/{utilities,medians,stats,reliability}.json, site/api/rates/<utility_id>.json, site/data/* (downloads)
"""

import json
import shutil
from datetime import datetime, timezone

import pandas as pd

from pipeline import openapi
from pipeline.doe import PLAUSIBLE
from pipeline.fetch import RAW, SHEET_URL, TABS
from pipeline.verify import LOOKBACK, NOT_AUTOMATED, SOURCES, TOLERANCE, evidence_stem

DATA = RAW.parent
ROOT = DATA.parent
SITE = ROOT / "site"
CLASSES = ["residential", "commercial", "industrial"]

RESOURCES = {
    "utilities": "Master list v0: one row per distribution utility in DOE's rate sheet, with PSGC coverage.",
    "rates": "Monthly retail rate per customer class and blended generation charge, PHP/kWh, as DOE compiled them.",
    "utility_areas": "Cities and municipalities each utility serves (as listed by the coverage source), with PSGC codes.",
    "qa/discrepancies": "DOE's figures checked against the utility's own published rate documents.",
    "qa/doe_sheet_issues": "Problems found in DOE's sheet itself: cross-tab disagreements, implausible and unparseable cells.",
    "qa/source_links": "Reachability of each link in DOE's SOURCES column.",
    "qa/unmatched_areas": "Areas the coverage source lists that could not be matched to one PSGC code.",
    "filled": "Cells DOE's sheet leaves empty (NDA or blank) that the utility's own rate document fills, with the document.",
    "outages/reliability_nea": "Power interruptions per electric cooperative per quarter (SAIFI, SAIDI, system loss), from NEA's quarterly compliance reports, year to date.",
    "qa/nea_reliability_issues": "NEA year-to-date figures that fall from one quarter to the next within a year: questions for NEA.",
}
DESCRIPTIONS = {
    "utility_id": "Stable slug ID, issued once from DOE's printed name and never reissued.",
    "doe_name": "Utility name exactly as DOE prints it (usually the acronym).",
    "name": "Full name, from the coverage source.",
    "rate_php_per_kwh": "Retail rate in PHP/kWh. Null where DOE has no data (see rate_note).",
    "generation_charge": "Blended generation charge in PHP/kWh for the utility-month (same on every class row).",
    "rate_note": "Empty for a clean number; NDA (DOE: no data available), blank, or unparsed:<cell>.",
    "psgc_province_codes": "Semicolon-separated 10-digit PSGC province codes of the franchise area. Empty if unsupported by the source.",
    "psgc_independent_city_codes": "PSGC codes of served cities that belong to no province (highly urbanised and independent component cities, all of NCR).",
    "psgc_code": "10-digit PSGC code of the city or municipality.",
    "month": "YYYY-MM.",
    "saifi": "System Average Interruption Frequency Index: sustained interruptions per customer, year to date (inferred). Null where NEA printed no figure.",
    "saidi_minutes": "System Average Interruption Duration Index: minutes without power per customer, year to date (inferred).",
    "period": "Quarter, YYYY-Qn; Q4 is the full year.",
    "missing": "NEA's code where it printed no figure (dns/nds: did not submit; nda: no data submitted; inc: incomplete).",
}


def field_type(s: pd.Series) -> str:
    if pd.api.types.is_integer_dtype(s):
        return "integer"
    if pd.api.types.is_float_dtype(s):
        return "number"
    return "string"


def datapackage(frames: dict[str, pd.DataFrame], stats: dict) -> dict:
    res = []
    for name, df in frames.items():
        res.append({
            "name": name.replace("/", "_"),
            "path": f"{name}.csv",
            "format": "csv",
            "mediatype": "text/csv",
            "description": RESOURCES[name],
            "schema": {"fields": [{"name": c, "type": field_type(df[c]), **({"description": DESCRIPTIONS[c]} if c in DESCRIPTIONS else {})}
                                  for c in df.columns]},
        })
    res[0]["schema"]["primaryKey"] = ["utility_id"]
    res[1]["schema"]["primaryKey"] = ["utility_id", "month", "customer_class"]
    res[1]["schema"]["foreignKeys"] = [{"fields": ["utility_id"], "reference": {"resource": "utilities", "fields": ["utility_id"]}}]
    return {
        "name": "ph-electricity-rates",
        "title": "Philippine electricity rates per distribution utility (prototype)",
        "description": "DOE's hand-compiled monthly rate sheet as a tidy, joinable dataset, with a master list of "
                       "distribution utilities keyed by a stable ID and PSGC codes, and automated checks against utilities' own rate documents.",
        "version": "0.1.0",
        "created": stats["built_at"],
        "licenses": [{"name": "ODC-BY-1.0", "path": "https://opendatacommons.org/licenses/by/1-0/",
                      "title": "Open Data Commons Attribution License (compilation); source data remains under its publishers' terms"}],
        "sources": [
            {"title": "DOE: Electricity rates per distribution utility (Google Sheet)", "path": SHEET_URL},
            {"title": "DOE: Data and prices, electricity", "path": "https://www.doe.gov.ph/data-and-prices/electricity"},
            {"title": "PSA PSGC via github.com/bendlikeabamboo/barangay (MIT)", "path": "https://github.com/bendlikeabamboo/barangay"},
            {"title": "Wikipedia: List of electric distribution utilities in the Philippines", "path": "https://en.wikipedia.org/wiki/List_of_electric_distribution_utilities_in_the_Philippines"},
        ],
        "resources": res,
    }


def coverage_stats(rates: pd.DataFrame, utilities: pd.DataFrame, areas: pd.DataFrame, disc: pd.DataFrame,
                   issues: pd.DataFrame, links: pd.DataFrame) -> dict:
    compiled = rates[rates.rate_note != "blank"]  # blank = month not compiled yet (whole column empty)
    months = sorted(compiled.month.unique())
    latest = months[-1]
    gen = compiled.drop_duplicates(["utility_id", "month"])
    checked = disc[~disc.status.str.startswith("doe_missing")]
    return {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "utilities": len(utilities),
        "utilities_by_type": utilities.du_type.value_counts().to_dict(),
        "months_compiled": len(months), "first_month": months[0], "latest_month": latest,
        "months_in_sheet_not_compiled": sorted(set(rates.month) - set(months)),
        "rate_cells": len(compiled),
        "rate_cells_with_value": int(compiled.rate_php_per_kwh.notna().sum()),
        "rate_cells_nda": int((compiled.rate_note == "NDA").sum()),
        "nda_share": round(float((compiled.rate_note == "NDA").mean()), 4),
        "nda_share_by_class": compiled.assign(nda=compiled.rate_note == "NDA").groupby("customer_class").nda.mean().round(4).to_dict(),
        "generation_cells": len(gen), "generation_nda_share": round(float((gen.generation_note == "NDA").mean()), 4),
        "latest_month_residential_coverage": round(float(compiled[(compiled.month == latest) & (compiled.customer_class == "residential")].rate_php_per_kwh.notna().mean()), 4),
        "utilities_with_any_residential_value": int(compiled[compiled.customer_class == "residential"].groupby("utility_id").rate_php_per_kwh.count().gt(0).sum()),
        "utilities_with_province_psgc": int((utilities.psgc_province_codes.fillna("") != "").sum()),
        "areas_listed": int(utilities.area_count_listed.sum()), "areas_matched_to_psgc": len(areas),
        "doe_sheet_issues": issues.check.value_counts().to_dict(),
        "source_checks": len(checked), "source_check_utilities": sorted(checked.utility_id.unique()),
        "source_check_mismatches": int((checked.status == "mismatch").sum()),
        "source_published_but_doe_blank": int(disc.status.str.startswith("doe_missing").sum()),
        "filled_cells": int((disc.status.str.startswith("doe_missing") & (disc.check_type == "same")).sum()),
        "doe_source_links": links.kind.value_counts().to_dict(),
        "doe_source_links_not_ok": int(((links.kind == "website") & (links.http_status != "200")).sum()),
    }


def write_json(path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # allow_nan=False: a NaN is not JSON, and browsers refuse the whole file
    path.write_text(json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False))


def api(rates: pd.DataFrame, utilities: pd.DataFrame, stats: dict) -> None:
    out = SITE / "api"
    shutil.rmtree(out, ignore_errors=True)
    r = rates.merge(utilities[["utility_id", "region"]], on="utility_id")
    # out-of-band values stay in the dataset (flagged in qa/doe_sheet_issues) but not in charts or medians
    ok = r.rate_php_per_kwh.between(*PLAUSIBLE)
    have = r[ok]
    gen_ok = r.generation_charge.between(*PLAUSIBLE)
    latest = have.sort_values("month").groupby(["utility_id", "customer_class"]).last()

    rows = []
    for u in utilities.fillna("").to_dict("records"):
        lr = {c: {"month": latest.loc[(u["utility_id"], c), "month"], "rate": latest.loc[(u["utility_id"], c), "rate_php_per_kwh"]}
              for c in CLASSES if (u["utility_id"], c) in latest.index}
        rows.append({k: u[k] for k in ("utility_id", "doe_name", "name", "du_type", "grid", "region", "psgc_region_code",
                                       "island", "psgc_province_codes", "psgc_provinces", "psgc_independent_city_codes", "independent_cities", "doe_source_url", "coverage_note")}
                    | {"latest": lr, "rates_url": f"/api/rates/{u['utility_id']}.json"})
    write_json(out / "utilities.json", {"generated": stats["built_at"], "count": len(rows), "utilities": rows})

    r["ok"], r["gen_ok"] = ok, gen_ok
    for uid, g in r.groupby("utility_id"):
        series = {c: [[m, v] for m, v in g[(g.customer_class == c) & g.ok][["month", "rate_php_per_kwh"]].itertuples(index=False)]
                  for c in CLASSES}
        gen = g.drop_duplicates("month")
        series["generation"] = [[m, v] for m, v in gen[gen.gen_ok][["month", "generation_charge"]].itertuples(index=False)]
        flagged = [{"month": m, "class": c, "value": v} for m, c, v in
                   g[g.rate_php_per_kwh.notna() & ~g.ok][["month", "customer_class", "rate_php_per_kwh"]].itertuples(index=False)]
        flagged += [{"month": m, "class": "generation", "value": v} for m, v in
                    gen[gen.generation_charge.notna() & ~gen.gen_ok][["month", "generation_charge"]].itertuples(index=False)]
        nda = {c: int((g[g.customer_class == c].rate_note == "NDA").sum()) for c in CLASSES}
        write_json(out / "rates" / f"{uid}.json", {"utility_id": uid, "unit": "PHP/kWh", "series": series,
                                                   "nda_months": nda, "flagged_by_qa": flagged})

    def med(df):
        return {c: {m: round(float(v), 4) for m, v in df[df.customer_class == c].groupby("month").rate_php_per_kwh.median().items()}
                for c in CLASSES}
    write_json(out / "medians.json", {
        "note": "Median across utilities with a value that month (NDA excluded). Unweighted: each utility counts once, regardless of customers served.",
        "national": med(have),
        "regions": {reg: med(df) for reg, df in have.groupby("region")},
        "counts": {c: {m: int(n) for m, n in have[have.customer_class == c].groupby("month").size().items()} for c in CLASSES},
    })
    write_json(out / "stats.json", stats)

    # one file with every utility's series, month-aligned, for the map and the all-utilities table
    months = sorted(rates.month.unique())
    idx = {m: i for i, m in enumerate(months)}
    panel = {c: {} for c in CLASSES + ["generation"]}
    for uid, g in r.groupby("utility_id"):
        for c in CLASSES:
            row = [None] * len(months)
            for m, v in g[(g.customer_class == c) & g.ok][["month", "rate_php_per_kwh"]].itertuples(index=False):
                row[idx[m]] = v
            panel[c][uid] = row
        row = [None] * len(months)
        gen = g.drop_duplicates("month")
        for m, v in gen[gen.gen_ok][["month", "generation_charge"]].itertuples(index=False):
            row[idx[m]] = v
        panel["generation"][uid] = row
    write_json(out / "panel.json", {"unit": "PHP/kWh", "months": months, "series": panel,
                                    "note": "null = NDA, blank, or a value flagged by QA (see /api/rates/<id>.json)"})


def reliability_api() -> None:
    """/api/reliability.json: NEA's SAIFI/SAIDI per electric cooperative and quarter, with medians and NEA's own flags."""
    from pipeline.reliability import PAGE

    df = pd.read_csv(DATA / "outages" / "reliability_nea.csv", keep_default_na=False, na_values=[""])
    issues = pd.read_csv(DATA / "qa" / "nea_reliability_issues.csv", keep_default_na=False)
    periods = sorted(df.period.unique())
    full = max(p for p in periods if p.endswith("Q4"))
    num = lambda v: None if pd.isna(v) else float(v)
    med = {p: {"saifi": num(g.saifi.median()), "saidi_minutes": num(g.saidi_minutes.median()),
               "with_figures": int(g.saidi_minutes.notna().sum()), "assessed": len(g)} for p, g in df.groupby("period")}
    write_json(SITE / "api" / "reliability.json", {
        "source": "National Electrification Administration, Compliance Report on the Performance of ECs (Appendix B)",
        "source_page": PAGE,
        "basis": "Year to date: figures rise through each year and restart each Q1, so Q4 is the full year. Inferred; NEA does not say.",
        "standards": {"on_grid": {"saifi": 25, "saidi_minutes": 2700}, "off_grid": {"saifi": 30, "saidi_minutes": 3375},
                      "note": "NEA's yearly standards per customer"},
        "covers": "Electric cooperatives only. ERC does not publish these indices for private utilities.",
        "periods": periods, "full_year": full[:4], "full_year_period": full,
        "source_urls": dict(df.drop_duplicates("period")[["period", "source_url"]].values),
        "medians": med,
        "utilities": {uid: [{"period": r.period, "saifi": num(r.saifi), "saidi_minutes": num(r.saidi_minutes),
                             "saifi_status": r.saifi_status, "saidi_status": r.saidi_status,
                             "missing": "" if pd.isna(r.missing) else r.missing, "page": int(r.page)}
                            for r in g.sort_values("period").itertuples()] for uid, g in df.groupby("utility_id")},
        "issues": {uid: [f"{r.measure.split('_')[0].upper()} {r.period} ({r.value:g}) is below {r.previous_period} ({r.previous_value:g})"
                         for r in g.itertuples()] for uid, g in issues.groupby("utility_id")},
    })


def col_letter(n: int) -> str:
    """1-based column number -> spreadsheet letters (1 -> A, 27 -> AA)."""
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def checks_api(rates: pd.DataFrame, utilities: pd.DataFrame, stats: dict) -> None:
    """/api/checks.json: every DOE-vs-source comparison with links to both sides (DOE's cell, the utility's page)."""
    disc = pd.read_csv(DATA / "qa" / "discrepancies.csv", dtype=str, keep_default_na=False)
    links = pd.read_csv(DATA / "qa" / "source_links.csv", dtype=str, keep_default_na=False).set_index("utility_id")
    months = sorted(rates.month.unique())
    gid = {kind: g for g, kind in TABS.items()}
    row_of = dict(utilities[["utility_id", "doe_sheet_row"]].astype(str).values)
    name_of = dict(utilities[["utility_id", "doe_name"]].values)
    frac = lambda s: [float(x) for x in s.split(",")] if s else None
    checks = []
    for d in disc.to_dict("records"):
        tab = "residential" if d["measure"] == "residential" else "generation"
        d["id_suffix"] = "" if d["check_type"] == "same" else f"-{d['check_type']}"
        cell = f"{col_letter(6 + months.index(d['month']))}{row_of[d['utility_id']]}"  # month columns start at F
        doc = d["doc"]
        checks.append({
            "id": f"{d['utility_id']}-{d['month']}-{d['measure']}{d['id_suffix']}", "utility_id": d["utility_id"], "doe_name": name_of[d["utility_id"]],
            "month": d["month"], "measure": d["measure"], "status": d["status"],
            "doe_value": float(d["doe_value"]) if d["doe_value"] else None,
            "doe_cell": f"{tab} tab, cell {cell}", "doe_cell_url": f"{SHEET_URL}/edit#gid={gid[tab]}&range={cell}",
            "source_value": float(d["source_value"]), "abs_diff": float(d["abs_diff"]) if d["abs_diff"] else None,
            "check_type": d["check_type"], "read_by": d["read_by"], "grounded": d["grounded"] == "True",
            "doc_status": d["doc_status"], "breakdown": json.loads(d["breakdown"]) if d["breakdown"] else None,
            "method": d["method"], "explain": d["explain"], "source_document": d["source_document"], "source_url": d["source_url"],
            "source_page_url": d["source_page_url"], "mirror": f"sources/{doc}", "sha256": d["sha256"],
            "page": int(d["page"]), "image": f"evidence/{evidence_stem(doc)}-p{d['page']}.webp",
            "row_text": d["row_text"], "row_box": frac(d["row_box"]), "value_box": frac(d["value_box"]),
        })
    sources = []
    for uid, s in SOURCES.items():
        mine = [c for c in checks if c["utility_id"] == uid and not c["status"].startswith("doe_missing")]
        link = links.loc[uid] if uid in links.index else None
        sources.append({**s, "utility_id": uid, "checks": len(mine), "matches": sum(c["status"] == "match" for c in mine),
                        "doe_source_url": link["doe_source_url"] if link is not None else "",
                        "doe_source_status": link["http_status"] if link is not None else ""})
    write_json(SITE / "api" / "checks.json", {
        "built": stats["built_at"], "tolerance": TOLERANCE, "lookback_months": LOOKBACK, "sheet_url": SHEET_URL,
        "sources": sources, "not_automated": NOT_AUTOMATED, "checks": checks,
        "link_audit": links.reset_index()[["utility_id", "doe_name", "doe_source_url", "kind", "http_status"]].to_dict("records"),
    })


def main() -> None:
    rates = pd.read_csv(DATA / "rates.csv", keep_default_na=False, na_values=[""])
    rates[["rate_note", "generation_note"]] = rates[["rate_note", "generation_note"]].fillna("")
    # PSGC codes are identifiers with leading zeros (Region I is 0100000000): never let them become numbers
    codes = {c: str for c in ("psgc_region_code", "psgc_province_codes", "psgc_independent_city_codes",
                              "psgc_code", "psgc_province_code", "http_status")}
    frames = {name: pd.read_csv(DATA / f"{name}.csv", dtype=codes, keep_default_na=False, na_values=[""]) for name in RESOURCES if name != "rates"}
    for name in ("utilities", "utility_areas"):
        frames[name] = frames[name].fillna({c: "" for c in codes if c in frames[name]})
    frames = {"utilities": frames["utilities"], "rates": rates, **{k: v for k, v in frames.items() if k != "utilities"}}
    stats = coverage_stats(rates, frames["utilities"], frames["utility_areas"], frames["qa/discrepancies"],
                           frames["qa/doe_sheet_issues"], frames["qa/source_links"])

    for name in ("utilities", "rates", "utility_areas"):
        frames[name].to_parquet(DATA / f"{name}.parquet", index=False)
    (DATA / "datapackage.json").write_text(json.dumps(datapackage(frames, stats), indent=2) + "\n")
    (DATA / "stats.json").write_text(json.dumps(stats, indent=2) + "\n")

    api(rates, frames["utilities"], stats)
    checks_api(rates, frames["utilities"], stats)
    reliability_api()
    downloads = SITE / "data"
    shutil.rmtree(downloads, ignore_errors=True)
    for name in list(RESOURCES) + ["utilities.parquet", "rates.parquet", "utility_areas.parquet", "datapackage.json"]:
        src = DATA / (name if "." in name else f"{name}.csv")
        (downloads / src.relative_to(DATA)).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(src, downloads / src.relative_to(DATA))
    openapi.main()
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
