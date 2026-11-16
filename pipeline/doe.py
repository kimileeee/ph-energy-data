"""Parse DOE's hand-compiled rate sheet into a tidy long table and a master-list base.

Inputs:  data/raw/doe_<gid>.csv (values as DOE displays them), data/raw/doe_rates.xlsx (hyperlinks)
Outputs: data/rates.csv, data/utilities.csv (id registry + DOE attributes), data/qa/doe_sheet_issues.csv
"""

import csv
import re
import unicodedata
from datetime import datetime
from pathlib import Path

import openpyxl
import pandas as pd

from pipeline.fetch import RAW, SHEET_URL, TABS

DATA = RAW.parent
HEADER_FIRST = "DU Name"
ATTRS = ["doe_name", "grid", "region", "island", "du_type"]
CLASSES = ["residential", "commercial", "industrial"]
NUM = re.compile(r"-?\d+(?:\.\d+)?")
# ponytail: plausibility band for a retail/generation rate in PHP/kWh; values outside are flagged, not dropped.
PLAUSIBLE = (1.0, 40.0)


def slug(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def month_key(label: str) -> str:
    return datetime.strptime(label.strip(), "%b-%y").strftime("%Y-%m")


def parse_value(raw: str) -> tuple[float | None, str]:
    """Return (value, note). note is '' for a clean number, else NDA / blank / unparsed:<raw>."""
    v = raw.strip()
    if NUM.fullmatch(v):
        return float(v), ""
    if v.upper() == "NDA":
        return None, "NDA"
    if not v:
        return None, "blank"
    return None, f"unparsed:{v}"


def read_tab(gid: str) -> tuple[list[str], list[dict]]:
    """Return (month keys, utility rows). A utility row is any row whose DU Type is PIOU or EC."""
    rows = list(csv.reader((RAW / f"doe_{gid}.csv").open(encoding="utf-8")))
    header = next(r for r in rows if r and r[0].strip() == HEADER_FIRST)
    first_month, trend = 5, header.index("Trend")
    months = [month_key(h) for h in header[first_month:trend]]
    src_col = header.index("SOURCES")
    out = []
    for i, r in enumerate(rows):
        if len(r) > 4 and r[4].strip() in ("PIOU", "EC"):
            out.append({
                "row": i + 1,  # 1-based, matches the xlsx row
                **dict(zip(ATTRS, (c.strip() for c in r[:5]))),
                "values": r[first_month:trend],
                "source_text": r[src_col].strip(),
            })
    return months, out


def hyperlinks(sheet_index: int) -> dict[int, str]:
    ws = openpyxl.load_workbook(RAW / "doe_rates.xlsx").worksheets[sheet_index]
    header_row = next(r for r in range(1, 40) if (ws.cell(r, 1).value or "").strip() == HEADER_FIRST)
    col = [c.value for c in ws[header_row]].index("SOURCES") + 1
    return {r: ws.cell(r, col).hyperlink.target for r in range(header_row + 1, ws.max_row + 1) if ws.cell(r, col).hyperlink}


def load_registry() -> dict[str, str]:
    """doe_name -> utility_id from the committed utilities.csv, so ids never change once issued."""
    path = DATA / "utilities.csv"
    if not path.exists():
        return {}
    return dict(pd.read_csv(path, dtype=str, keep_default_na=False)[["doe_name", "utility_id"]].values)


def main() -> None:
    tabs = {}
    for idx, (gid, kind) in enumerate(TABS.items()):
        months, rows = read_tab(gid)
        links = hyperlinks(idx)
        for r in rows:
            r["source_url"] = links.get(r["row"], "")
        tabs[kind] = (months, rows)

    base_months, base = tabs["residential"]
    issues = []
    for kind, (months, rows) in tabs.items():
        assert months == base_months, f"{kind}: month columns differ from residential"
        assert len(rows) == len(base), f"{kind}: {len(rows)} utilities vs {len(base)}"
        for b, r in zip(base, rows):
            for a in ATTRS:
                if r[a] != b[a]:
                    issues.append({"check": "attribute differs across tabs", "doe_name": b["doe_name"], "tab": kind,
                                   "field": a, "value": r[a], "residential_value": b[a], "sheet_row": r["row"]})

    registry = load_registry()
    seen = set()
    utilities = []
    for b in base:
        uid = registry.get(b["doe_name"]) or slug(b["doe_name"])
        assert uid not in seen, f"duplicate utility_id {uid}"
        seen.add(uid)
        b["utility_id"] = uid
        utilities.append({"utility_id": uid, **{a: b[a] for a in ATTRS},
                          "doe_source_text": b["source_text"], "doe_source_url": b["source_url"],
                          "doe_sheet_row": b["row"]})

    records = []
    for i, b in enumerate(base):
        gen_vals = tabs["generation"][1][i]["values"]
        for kind in CLASSES:
            r = tabs[kind][1][i]
            for m, raw, graw in zip(base_months, r["values"], gen_vals):
                rate, note = parse_value(raw)
                gen, gnote = parse_value(graw)
                records.append({"utility_id": b["utility_id"], "month": m, "customer_class": kind,
                                "rate_php_per_kwh": rate, "generation_charge": gen,
                                "rate_note": note, "generation_note": gnote,
                                "doe_source": r["source_url"] or r["source_text"]})
                for val, n, what in ((rate, note, kind), (gen, gnote, "generation")):
                    if n.startswith("unparsed"):
                        issues.append({"check": "unparseable cell", "doe_name": b["doe_name"], "tab": what,
                                       "field": m, "value": n.split(":", 1)[1], "residential_value": "", "sheet_row": r["row"]})
                    elif val is not None and not PLAUSIBLE[0] <= val <= PLAUSIBLE[1]:
                        issues.append({"check": f"outside {PLAUSIBLE[0]}-{PLAUSIBLE[1]} PHP/kWh", "doe_name": b["doe_name"],
                                       "tab": what, "field": m, "value": val, "residential_value": "", "sheet_row": r["row"]})

    rates = pd.DataFrame(records)
    # generation repeats on each class row; dedupe its issues
    qa = pd.DataFrame(issues).drop_duplicates()
    (DATA / "qa").mkdir(exist_ok=True)
    rates.to_csv(DATA / "rates.csv", index=False)
    pd.DataFrame(utilities).to_csv(DATA / "utilities_doe.csv", index=False)
    qa.to_csv(DATA / "qa" / "doe_sheet_issues.csv", index=False)
    print(f"rates: {len(rates):,} rows, {rates.utility_id.nunique()} utilities, {len(base_months)} months; issues: {len(qa)}")


if __name__ == "__main__":
    main()
