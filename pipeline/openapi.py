"""site/openapi.json (OpenAPI 3.1) for the static JSON API, and site/api/catalog.json (the bulk files, with sizes).

The spec is generated so its utility_id enum, examples and file list always match what was built.
Examples are cut from the real responses.
"""

import json

import pandas as pd

from pipeline.fetch import RAW

DATA = RAW.parent
SITE = DATA.parent / "site"

NUM = {"type": "number"}
NUM_OR_NULL = {"type": ["number", "null"]}
STR = {"type": "string"}
MONTH = {"type": "string", "pattern": r"^\d{4}-\d{2}$", "description": "YYYY-MM", "examples": ["2026-10"]}
SERIES = {"type": "array", "description": "[month, PHP/kWh] pairs, months with a value only",
          "items": {"type": "array", "prefixItems": [MONTH, NUM], "minItems": 2, "maxItems": 2}}
CLASS_KEYS = ["residential", "commercial", "industrial"]


def schemas() -> dict:
    latest = {"type": "object", "properties": {"month": MONTH, "rate": NUM}, "required": ["month", "rate"]}
    by_class = lambda item: {"type": "object", "properties": {c: item for c in CLASS_KEYS}}
    return {
        "Utility": {
            "type": "object", "description": "One distribution utility in DOE's rate sheet (master list v0).",
            "properties": {
                "utility_id": {**STR, "description": "Stable ID, issued once from the name DOE prints."},
                "doe_name": {**STR, "description": "Name exactly as DOE prints it (usually the acronym)."},
                "name": {**STR, "description": "Full name, from the coverage source. Empty if unmatched."},
                "du_type": {"enum": ["PIOU", "EC"], "description": "Private investor-owned utility or electric cooperative."},
                "grid": {"enum": ["ONGRID", "OFFGRID"]},
                "region": {**STR, "description": "Region as DOE labels it (e.g. 'Region 4A', 'NCR', 'BARMM')."},
                "psgc_region_code": {**STR, "description": "10-digit PSGC code of that region."},
                "island": {"enum": ["Luzon", "Visayas", "Mindanao"]},
                "psgc_province_codes": {**STR, "description": "Semicolon-separated 10-digit PSGC province codes of the franchise area."},
                "psgc_provinces": {**STR, "description": "Their names, same order."},
                "psgc_independent_city_codes": {**STR, "description": "Served cities that belong to no province (HUCs, ICCs, NCR)."},
                "independent_cities": {**STR, "description": "Their names, same order."},
                "doe_source_url": {**STR, "description": "The link in DOE's SOURCES column."},
                "coverage_note": {**STR, "description": "Caveats on the coverage match."},
                "latest": {**by_class(latest), "description": "Most recent month with a plausible value, per class."},
                "rates_url": {**STR, "description": "Path of this utility's rates file."},
            },
        },
        "UtilityList": {"type": "object", "properties": {
            "generated": {**STR, "format": "date-time"}, "count": {"type": "integer"},
            "utilities": {"type": "array", "items": {"$ref": "#/components/schemas/Utility"}}}},
        "Rates": {"type": "object", "description": "Monthly series for one utility, PHP/kWh, as DOE entered them.", "properties": {
            "utility_id": STR, "unit": {"const": "PHP/kWh"},
            "series": {"type": "object", "properties": {**{c: SERIES for c in CLASS_KEYS}, "generation": SERIES}},
            "nda_months": {**by_class({"type": "integer"}), "description": "Months DOE marked NDA (no data available)."},
            "flagged_by_qa": {"type": "array", "description": "Values left out of the series as implausible (outside 1-40 PHP/kWh). Still in rates.csv.",
                              "items": {"type": "object", "properties": {"month": MONTH, "class": STR, "value": NUM}}}}},
        "Panel": {"type": "object", "description": "Every utility's series in one file, aligned to `months`.", "properties": {
            "unit": {"const": "PHP/kWh"}, "months": {"type": "array", "items": MONTH},
            "series": {"type": "object", "description": "class -> utility_id -> values (same order as months; null = no plausible value)",
                       "additionalProperties": {"type": "object", "additionalProperties": {"type": "array", "items": NUM_OR_NULL}}},
            "note": STR}},
        "Medians": {"type": "object", "description": "Unweighted medians across utilities with a value that month.", "properties": {
            "note": STR,
            "national": {"type": "object", "description": "class -> month -> median", "additionalProperties": {"type": "object", "additionalProperties": NUM}},
            "regions": {"type": "object", "description": "region -> class -> month -> median"},
            "counts": {"type": "object", "description": "class -> month -> number of utilities in the national median"}}},
        "Check": {"type": "object", "description": "One DOE figure compared with the utility's own document.", "properties": {
            "id": STR, "utility_id": STR, "doe_name": STR, "month": MONTH, "measure": {"enum": ["residential", "generation"]},
            "status": {**STR, "description": "match | mismatch | doe_missing (blank|NDA)"},
            "doe_value": NUM_OR_NULL, "doe_cell": STR, "doe_cell_url": {**STR, "description": "Opens the exact cell in DOE's Google Sheet."},
            "source_value": NUM, "abs_diff": NUM_OR_NULL, "method": STR, "explain": STR,
            "source_document": STR, "source_url": {**STR, "description": "The utility's original file."},
            "source_page_url": STR, "mirror": {**STR, "description": "Copy of the file kept by this site."},
            "sha256": STR, "page": {"type": "integer"},
            "image": {**STR, "description": "Rendered page image."},
            "row_text": STR,
            "row_box": {"type": "array", "items": NUM, "description": "x0, top, x1, bottom as fractions of the page"},
            "value_box": {"type": "array", "items": NUM, "description": "Same, for the value itself"}}},
        "Checks": {"type": "object", "properties": {
            "built": STR, "tolerance": NUM, "lookback_months": {"type": "integer"}, "sheet_url": STR,
            "sources": {"type": "array", "items": {"type": "object"}}, "not_automated": {"type": "array", "items": {"type": "object"}},
            "checks": {"type": "array", "items": {"$ref": "#/components/schemas/Check"}},
            "link_audit": {"type": "array", "items": {"type": "object"}}}},
        "Stats": {"type": "object", "description": "Coverage numbers for this build.", "additionalProperties": True},
        "ReliabilityQuarter": {"type": "object", "description": "One cooperative, one quarter, as NEA printed it (year to date).", "properties": {
            "period": {**STR, "pattern": r"^\d{4}-Q[1-4]$", "examples": ["2025-Q4"]},
            "saifi": {**NUM_OR_NULL, "description": "Sustained interruptions per customer, year to date. Null where NEA printed no figure."},
            "saidi_minutes": {**NUM_OR_NULL, "description": "Minutes without power per customer, year to date."},
            "saifi_status": {**STR, "description": "NEA's flag: C (compliant) or NC (non-compliant; also given when no data was sent)."},
            "saidi_status": STR,
            "missing": {**STR, "description": "NEA's code where it printed no figure (dns, nds, nda, inc), else empty."},
            "page": {"type": "integer", "description": "Page of NEA's report the row is on."}}},
        "Reliability": {"type": "object", "description": "SAIFI and SAIDI per electric cooperative per quarter, from NEA's compliance reports.", "properties": {
            "source": STR, "source_page": STR, "basis": STR, "covers": STR,
            "standards": {"type": "object", "description": "NEA's yearly standards per customer, on-grid and off-grid."},
            "periods": {"type": "array", "items": STR}, "full_year": STR, "full_year_period": STR,
            "source_urls": {"type": "object", "description": "period -> NEA's report file", "additionalProperties": STR},
            "medians": {"type": "object", "description": "period -> median SAIFI and SAIDI across cooperatives with a figure (unweighted)"},
            "utilities": {"type": "object", "description": "utility_id -> quarters",
                          "additionalProperties": {"type": "array", "items": {"$ref": "#/components/schemas/ReliabilityQuarter"}}},
            "issues": {"type": "object", "description": "utility_id -> year-to-date figures that fall within a year (questions for NEA)",
                       "additionalProperties": {"type": "array", "items": STR}}}},
        "Municipalities": {"type": "object", "description": "GeoJSON FeatureCollection of municipalities and cities, with the utilities serving each.", "properties": {
            "type": {"const": "FeatureCollection"},
            "features": {"type": "array", "items": {"type": "object", "properties": {
                "type": {"const": "Feature"}, "geometry": {"type": "object", "description": "Polygon or MultiPolygon, WGS84 lon/lat"},
                "properties": {"type": "object", "properties": {
                    "c": {**STR, "description": "10-digit PSGC code"}, "n": {**STR, "description": "name"}, "p": {**STR, "description": "province ('' for independent cities)"},
                    "u": {**STR, "description": "JSON-encoded [utility_id, qualifier] pairs"},
                    "b": {"type": "array", "items": NUM, "description": "bounding box [west, south, east, north]"}}}}}}}},
    }


def trim(obj, n=2):
    """A short example from a real response: lists cut to n items, nested too."""
    if isinstance(obj, list):
        return [trim(x, n) for x in obj[:n]]
    if isinstance(obj, dict):
        return {k: trim(v, n) for k, v in list(obj.items())[: 12]}
    return obj


def example(path: str, n: int = 2):
    return trim(json.loads((SITE / path).read_text()), n)


def main() -> None:
    utilities = pd.read_csv(DATA / "utilities.csv", dtype=str, keep_default_na=False)
    ids = sorted(utilities.utility_id)
    ok = lambda ref, ex, desc="OK": {"200": {"description": desc, "content": {"application/json": {
        "schema": {"$ref": f"#/components/schemas/{ref}"}, "example": ex}}}}
    def get(op, summary, desc, ref, ex, tag, params=None):
        responses = ok(ref, ex)
        if params:  # a path parameter outside the enum is a file that doesn't exist
            responses["404"] = {"description": "No such utility_id"}
        return {"get": {"operationId": op, "summary": summary, "description": desc, "tags": [tag],
                        **({"parameters": params} if params else {}), "responses": responses}}
    spec = {
        "openapi": "3.1.0",
        "info": {
            "title": "Philippine electricity rates (prototype)",
            "version": "0.1.0",
            "summary": "DOE's monthly rate sheet as static JSON: utilities, monthly rates, medians, source checks, and NEA's power-interruption indices.",
            "description": "Every endpoint is a static file: no key, no rate limit, cacheable. Files regenerate when the pipeline runs. "
                           "Join to LGUs on PSGC codes. Bulk CSV/Parquet and a Frictionless datapackage are listed in /api/catalog.json.",
            "license": {"name": "ODC-BY-1.0 (data), MIT (code)", "url": "https://opendatacommons.org/licenses/by/1-0/"},
        },
        "servers": [{"url": ".", "description": "This site"}],
        "security": [],  # public static files: no authentication
        "tags": [{"name": "Utilities", "description": "The master list: one stable ID per distribution utility, with PSGC coverage."},
                 {"name": "Rates", "description": "Monthly PHP/kWh per customer class, as DOE compiled them, and medians."},
                 {"name": "Checks", "description": "DOE's figures against utilities' own rate documents."},
                 {"name": "Reliability", "description": "Power interruptions per electric cooperative (NEA's SAIFI and SAIDI)."},
                 {"name": "Geography", "description": "Municipal boundaries joined to the utilities serving them."},
                 {"name": "Meta", "description": "Coverage numbers for the build."}],
        "paths": {
            "/api/utilities.json": get("listUtilities", "List utilities", "Master list v0 with each utility's latest rate per class.",
                                       "UtilityList", example("api/utilities.json"), "Utilities"),
            "/api/rates/{utility_id}.json": get("getRates", 
                "Rates for one utility", "Monthly residential, commercial and industrial rates and the generation charge.",
                "Rates", example("api/rates/meralco.json", 3), "Rates",
                [{"name": "utility_id", "in": "path", "required": True, "description": "From /api/utilities.json",
                  "schema": {"type": "string", "enum": ids}, "example": "meralco"}]),
            "/api/panel.json": get("getPanel", "Every utility, every month", "All series in one file, month-aligned (drives the map).",
                                   "Panel", example("api/panel.json", 2), "Rates"),
            "/api/medians.json": get("getMedians", "National and regional medians", "Unweighted medians per class and month.",
                                     "Medians", example("api/medians.json", 2), "Rates"),
            "/api/checks.json": get("getChecks", "Source checks", "DOE's figures compared with utilities' own rate documents, with evidence locations.",
                                    "Checks", example("api/checks.json", 1), "Checks"),
            "/api/reliability.json": get("getReliability", "Power interruptions per cooperative",
                                         "NEA's quarterly SAIFI and SAIDI for every electric cooperative, year to date, with medians and NEA's compliance flags.",
                                         "Reliability", example("api/reliability.json", 2), "Reliability"),
            "/geo/municipalities.geojson": get("getMunicipalities", "Municipal boundaries", "PSGC 3Q 2026 municipalities/cities as GeoJSON (WGS84), joined to the utilities serving each.",
                                            "Municipalities", {"type": "FeatureCollection", "features": [{"type": "Feature",
                                                "geometry": {"type": "Polygon", "coordinates": [[[120.59, 16.38], [120.61, 16.38], [120.6, 16.42], [120.59, 16.38]]]},
                                                "properties": {"c": "1430300000", "n": "City of Baguio", "p": "Benguet", "u": "[[\"beneco\", \"\"]]", "b": [120.55, 16.36, 120.64, 16.44]}}]},
                                            "Geography"),
            "/api/stats.json": get("getStats", "Coverage numbers", "Counts and shares for this build.", "Stats", example("api/stats.json", 3), "Meta"),
        },
        "components": {"schemas": schemas()},
    }
    (SITE / "openapi.json").write_text(json.dumps(spec, indent=1, ensure_ascii=False))

    dp = json.loads((DATA / "datapackage.json").read_text())
    files = []
    for r in dp["resources"]:
        path = DATA / r["path"]
        rows = sum(1 for _ in path.open()) - 1
        entry = {"name": r["name"], "description": r["description"], "rows": rows, "fields": len(r["schema"]["fields"]),
                 "formats": [{"format": "CSV", "path": f"data/{r['path']}", "bytes": path.stat().st_size}]}
        pq = path.with_suffix(".parquet")
        if pq.exists():
            entry["formats"].append({"format": "Parquet", "path": f"data/{r['path'][:-4]}.parquet", "bytes": pq.stat().st_size})
        files.append(entry)
    (SITE / "api" / "catalog.json").write_text(json.dumps({
        "datapackage": "data/datapackage.json", "openapi": "openapi.json", "files": files}, separators=(",", ":")))
    print(f"openapi: {len(spec['paths'])} paths; catalog: {len(files)} files")


if __name__ == "__main__":
    main()
