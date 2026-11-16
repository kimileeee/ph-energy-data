"""Franchise coverage: Wikipedia's 'areas served' lists matched to PSGC, rolled up to provinces.

Source of areas: en.wikipedia.org "List of electric distribution utilities in the Philippines"
(cites DOE's DU profiles and Distribution Development Plans). We only record what that table
lists; a utility with no match keeps empty PSGC columns and a note saying why.

Inputs:  data/utilities_doe.csv, data/raw/wikipedia/du_list.wiki, data/raw/psgc/*.json
Outputs: data/utilities.csv (master list v0), data/utility_areas.csv, data/qa/unmatched_areas.csv
"""

import csv
import re
import unicodedata

import pandas as pd

from pipeline.fetch import RAW, WIKI_TITLE

DATA = RAW.parent
WIKI_URL = f"https://en.wikipedia.org/wiki/{WIKI_TITLE}"
# DOE's region labels -> PSGC region codes
REGION_PSGC = {"NCR": "1300000000", "CAR": "1400000000", "Region 1": "0100000000", "Region 2": "0200000000",
               "Region 3": "0300000000", "Region 4A": "0400000000", "Region 4B": "1700000000", "Region 5": "0500000000",
               "Region 6": "0600000000", "Region 7": "0700000000", "Region 8": "0800000000", "NIR": "1800000000",
               "Region 9": "0900000000", "Region 10": "1000000000", "Region 11": "1100000000", "Region 12": "1200000000",
               "CARAGA": "1600000000", "BARMM": "1900000000"}
ROMAN = {"1": "I", "2": "II", "3": "III", "4": "IV", "5": "V"}

# DOE name -> Wikipedia acronym, where normalisation alone can't bridge them.
# Each is a judgement; the note column in utilities.csv repeats it.
CROSSWALK = {
    "ALECO": ("ALECO/APEC", ""),
    "IEEC": ("IEC", ""),
    "MORE Power": ("MEPC", ""),
    "CENECO/NEPC": ("NEPC", "CENECO's franchise passed to NEPC; areas are NEPC's"),
    "PPALMA": ("COTELCO – PPALMA", ""),
    "NEECO II  AREA I": ("NEECO II – Area 1", ""),
    "NEECO II  AREA II": ("NEECO II – Area 2", ""),
    "LEYECO 1/DORELCO": ("DORELCO", "LEYECO I merged into DORELCO; areas are DORELCO's"),
    "DANECO/NORDECO": ("NORDECO", "areas are NORDECO's only; DANECO's own franchise is not listed in the source"),
}


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"\b(science |island garden )?city of\b|\bcity\b", " ", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def full_key(s: str) -> str:
    """Like norm() but keeps 'city', so 'Quezon City' and 'City of Quezon' differ from the town of Quezon."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"^city of (.*)$", r"\1 city", s.strip())
    return re.sub(r"[^a-z0-9]+", "", s)


def acronym_key(s: str) -> str:
    s = re.sub(r"(\D)(\d)$", lambda m: m.group(1) + " " + ROMAN[m.group(2)], s.strip())
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def wiki_rows() -> list[dict]:
    """Rows of the wikitable: name, acronym, type, grid, customers, areas (list of link targets / items)."""
    text = (RAW / "wikipedia" / "du_list.wiki").read_text()
    table = text[text.index("\n|-") : text.index("\n|}")]
    rows = []
    for chunk in table.split("\n|-")[1:]:
        lines = chunk.split("\n")
        starts = [i for i, l in enumerate(lines) if l.startswith("|") and not l.startswith(("|-", "|}", "|+"))]
        if len(starts) < 6:
            continue
        cells = [re.sub(r'^\|\s*(class="[^"]*"\s*\|\s*)?', "", lines[i]).strip() for i in starts[:5]]
        area_text = re.sub(r"<ref.*?(</ref>|/>)", "", "\n".join(lines[starts[5]:]), flags=re.S)
        name = unlink(re.sub(r"\{\{efn.*", "", cells[0])).strip()
        rows.append({"wiki_name": name, "wiki_acronym": cells[1], "wiki_type": cells[2], "areas": area_items(area_text)})
    return rows


def unlink(s: str) -> str:
    return re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]+)\]\]", r"\1", s)


def area_items(block: str) -> list[tuple[str, str]]:
    """(area as listed, qualifier) per line; qualifier is the footnote text, e.g. 'Some areas only'."""
    items = []
    for line in block.split("\n"):
        line = re.sub(r'^\s*\|\s*(class="[^"]*"\s*\|\s*)?', "", line).strip()
        line = re.sub(r"^title\s*=\s*List \(\d+\)\|?", "", line)
        if not line or line.startswith("}}") or line == "{{Collapsible list":
            continue
        links = [(target.split("|")[0], efn_text(efn)) for target, efn in LINK.findall(line)]
        if not links:
            m = re.search(r"\{\{efn\|([^{}]*)\}\}", line)
            links = [(line[: m.start()] if m else line, efn_text(m.group(1)) if m else "")]
        items += links
    return [(a.strip(" }"), q) for a, q in items if a.strip(" }")]


LINK = re.compile(r"\[\[([^\]]+)\]\](?:\{\{efn\|((?:[^{}]|\[\[[^\]]*\]\])*)\}\})?")


def efn_text(body: str) -> str:
    """Footnote body -> readable qualifier. Named footnotes are defined once and reused by name."""
    if not body:
        return ""
    name, _, text = body.partition("|") if body.startswith("name=") else ("", "", body)
    name = name.removeprefix("name=")
    return unlink(text).strip() or EFN.get(name, name)


# named footnotes defined once in the article and reused by name
EFN = {"some": "Some areas only", "otg": "Off-the-grid", "sotg": "Some areas are off-the-grid",
       "cfez": "Clark Freeport and Special Economic Zone", "sbfz": "Subic Bay Freeport Zone"}


def psgc():
    """(provinces {code: name}, municipality/city name key -> candidates with their province code)."""
    places = {p["psgc_id"]: p for p in csv.DictReader((RAW / "psgc" / "places.csv").open())}

    def up(code: str, kind: str) -> str:
        while code in places and places[code]["type"] != kind:
            code = places[code]["parent_psgc_id"]
        return code if code in places else ""

    provinces = {c: p["name"] for c, p in places.items() if p["type"] == "province"}
    by_key: dict[str, list[dict]] = {}
    for c, p in places.items():
        if p["type"] in ("municipality", "component_city", "highly_urbanized_city", "independent_component_city"):
            by_key.setdefault(norm(p["name"]), []).append(
                {"code": c, "name": p["name"], "province": up(c, "province"), "region": up(c, "region")})
    return provinces, by_key


def match_areas(areas: list[str], provinces: dict, by_key: dict) -> tuple[list[dict], list[str]]:
    prov_key = {norm(name): code for code, name in provinces.items()}
    matched, pending, unmatched = [], [], []
    for item, qualifier in areas:
        name, _, hint = item.partition(",")
        cands = by_key.get(norm(name), [])
        if hint and norm(hint) in prov_key:
            # a named province is binding: no candidate there means no match, not a guess elsewhere
            cands = [c for c in cands if c["province"] == prov_key[norm(hint)]]
        cands = list({c["code"]: c for c in cands}.values())
        exact = [c for c in cands if full_key(c["name"]) == full_key(name)]
        if len(cands) > 1 and len(exact) == 1:
            cands = exact
        if len(cands) == 1:
            matched.append(((item, qualifier), cands[0]))
        elif cands:
            pending.append(((item, qualifier), cands))
        else:
            unmatched.append((item, qualifier))
    # second pass: an ambiguous name resolves if exactly one candidate sits in a province this utility already serves
    served = {c["province"] for _, c in matched if c["province"]}
    regions = {c["region"] for _, c in matched}
    for item, cands in pending:
        inside = [c for c in cands if c["province"] in served]
        if len(inside) != 1:  # independent cities have no province; fall back to region
            inside = [c for c in cands if c["region"] in regions]
        if len(inside) == 1:
            matched.append((item, inside[0]))
        else:
            unmatched.append(item)
    return [{"area_as_listed": i, "qualifier": q, "psgc_code": c["code"], "psgc_name": c["name"],
             "psgc_province_code": c["province"], "psgc_region_code": c["region"]} for (i, q), c in matched], unmatched


def main() -> None:
    doe = pd.read_csv(DATA / "utilities_doe.csv", dtype=str, keep_default_na=False)
    wiki = {acronym_key(r["wiki_acronym"]): r for r in wiki_rows()}
    provinces, by_key = psgc()

    out, areas_out, unmatched_out = [], [], []
    for u in doe.to_dict("records"):
        target, note = CROSSWALK.get(u["doe_name"], (u["doe_name"], ""))
        w = wiki.get(acronym_key(target))
        row = {**u, "psgc_region_code": REGION_PSGC[u["region"]], "name": "", "wiki_acronym": "", "psgc_province_codes": "", "psgc_provinces": "", "psgc_independent_city_codes": "", "independent_cities": "",
               "area_count_listed": 0, "area_count_matched": 0, "coverage_source": "", "coverage_note": note}
        if not w:
            row["coverage_note"] = "not found in the coverage source; left empty"
        else:
            matched, unmatched = match_areas(w["areas"], provinces, by_key)
            codes = sorted({m["psgc_province_code"] for m in matched if m["psgc_province_code"]})
            # highly urbanised / independent cities sit under the region, not a province
            cities = sorted({(m["psgc_code"], m["psgc_name"]) for m in matched if not m["psgc_province_code"]})
            row.update(name=w["wiki_name"], wiki_acronym=w["wiki_acronym"],
                       psgc_province_codes=";".join(codes), psgc_provinces=";".join(provinces[c] for c in codes),
                       psgc_independent_city_codes=";".join(c for c, _ in cities), independent_cities=";".join(n for _, n in cities),
                       area_count_listed=len(w["areas"]), area_count_matched=len(matched), coverage_source=WIKI_URL)
            if not w["areas"]:
                row["coverage_note"] = "; ".join(filter(None, [note, "source lists no areas"]))
            elif not codes:
                row["coverage_note"] = "; ".join(filter(None, [note, "areas are independent cities only (no province)"]))
            areas_out += [{"utility_id": u["utility_id"], **m} for m in matched]
            unmatched_out += [{"utility_id": u["utility_id"], "area_as_listed": a, "qualifier": q} for a, q in unmatched]
        out.append(row)

    cols = ["utility_id", "doe_name", "name", "wiki_acronym", "du_type", "grid", "region", "psgc_region_code", "island",
            "psgc_province_codes", "psgc_provinces", "psgc_independent_city_codes", "independent_cities", "area_count_listed", "area_count_matched",
            "doe_source_url", "doe_source_text", "coverage_source", "coverage_note", "doe_sheet_row"]
    pd.DataFrame(out)[cols].to_csv(DATA / "utilities.csv", index=False)
    pd.DataFrame(areas_out).to_csv(DATA / "utility_areas.csv", index=False)
    pd.DataFrame(unmatched_out, columns=["utility_id", "area_as_listed", "qualifier"]).to_csv(DATA / "qa" / "unmatched_areas.csv", index=False)
    areas = pd.DataFrame(areas_out).merge(pd.DataFrame(out)[["utility_id", "psgc_region_code"]], on="utility_id", suffixes=("", "_doe"))
    outside = areas[areas.psgc_region_code != areas.psgc_region_code_doe]
    outside.to_csv(DATA / "qa" / "areas_outside_doe_region.csv", index=False)
    print(f"matched areas outside the utility's DOE region: {len(outside)}")
    with_prov = sum(1 for r in out if r["psgc_province_codes"])
    print(f"utilities: {len(out)}, with province PSGC: {with_prov}; areas matched {len(areas_out)}, unmatched {len(unmatched_out)}")


if __name__ == "__main__":
    main()
