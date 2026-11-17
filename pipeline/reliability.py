"""Reliability per electric cooperative: NEA's quarterly "Compliance Report on the Performance of ECs", Appendix B
("Technical Parameter Indices": SAIFI, SAIDI and system/feeder loss per EC, each with NEA's compliance flag).

Source: NEA's EC Classifications page (nea.gov.ph/ec-classifications/) lists the reports from public Google Drive
folders; the files are fetched from Drive, which needs no browser check (nea.gov.ph itself sits behind Cloudflare).
The reports are text PDFs, so a parser that raises when Appendix B's rows stop matching.

Basis: NEA defines SAIFI (interruptions per customer) and SAIDI (minutes per customer) against yearly standards
(on-grid 25 and 2,700; off-grid 30 and 3,375). The quarterly figures rise through each year and restart each Q1,
so they read as year to date and Q4 is the year's figure. NEA doesn't say so; `basis` records it as inferred, and a
figure that falls from one quarter to the next within a year is flagged in data/qa/nea_reliability_issues.csv.

Outputs: data/outages/reliability_nea.csv, data/qa/nea_reliability_issues.csv
Run:     uv run python -m pipeline.reliability [--offline]
"""

import hashlib
import re
import sys

import httpx
import pandas as pd
import pdfplumber

from pipeline.coverage import acronym_key
from pipeline.fetch import RAW, UA

DATA = RAW.parent
SRC = RAW / "sources" / "nea"  # gitignored
OFFLINE = "--offline" in sys.argv
PAGE = "https://nea.gov.ph/ec-classifications/"
# period -> Google Drive file id, as listed on NEA's EC Classifications page (2024-2026 folders)
REPORTS = {
    "2024-Q1": "1Xne0kf420Ln1uNLkGEG3gAwffYSJIWG4", "2024-Q2": "1bJHA-92K2-fHZkobl-oyOuOs__Tne2nf",
    "2024-Q3": "1gV7iOD-YfaJP1fyUvlKtIMQBZYRhAlRs", "2024-Q4": "1MGQ-dgeT2i5NGjGE-_IZdxsgNYLVU2yC",
    "2025-Q1": "1_Tz1u1C0ZH-xr6TG8o8RhVoJA5_WO4uJ", "2025-Q2": "18EPcIT_taVr9nMg09B3J9nzJoNw-tlt2",
    "2025-Q3": "1G_tiLdBdU9HlT1-__d_KEPgioAk3NiK1", "2025-Q4": "1ZhgRg7aefS6KbO-j32FT0udlKee9Sh4Q",
    "2026-Q1": "18EiEEd9vLVW5Z4cwyvBYmNitSXhvX5UE", "2026-Q2": "1V3LiDjQk-7eamX5eCyWahSNnpG9pyA7p",
}
# NEA name -> utility_id, where the name alone doesn't reach DOE's
CROSSWALK = {"CENECO": "ceneco-nepc"}  # DOE prints CENECO/NEPC; NEA still assesses CENECO
FLAG = r"(C|NC|[a-z]{2,4})"  # compliance flag; a lowercase code where NEA has no figure
ROW = re.compile(rf"^(.+?)\s+(\S+)\s+{FLAG}\s+(\S+)\s+{FLAG}\s+(\S+?)%?\s+{FLAG}$")
NOT_ROWS = re.compile(r"^(Region|CAR|BARMM|CARAGA|Negros|NIR|Appendix|System|Feeder|Electric|Loss|Note|\d+ \|)")


def number(s: str) -> float | None:
    """'1,512.56' -> 1512.56; 'dns' (did not submit, 2024), 'nda' (no data submitted, 2025-), 'inc' -> None."""
    return None if is_code(s) else float(s.replace(",", ""))


def is_code(s: str) -> bool:
    """A word where a figure belongs: NEA's 'dns'/'nda'/'inc' (and the odd 'nds'), kept as printed in `missing`."""
    return bool(re.fullmatch(r"[A-Za-z/-]+", s))


def parse(pdf_path, period: str) -> list[dict]:
    rows, region = [], ""
    with pdfplumber.open(pdf_path) as pdf:
        pages = [(i, p.extract_text() or "") for i, p in enumerate(pdf.pages, 1)]
    pages = [(i, t) for i, t in pages if "Appendix B" in t[:80]]
    if not pages:
        raise ValueError(f"{period}: no 'Appendix B' page; layout changed?")
    for pno, text in pages:
        for line in (l.strip() for l in text.splitlines()):
            if m := ROW.match(line):
                rows.append({"nea_name": m.group(1), "region_as_printed": region, "period": period,
                             "saifi": number(m.group(2)), "saifi_status": m.group(3),
                             "saidi_minutes": number(m.group(4)), "saidi_status": m.group(5),
                             "loss_pct": number(m.group(6)), "loss_status": m.group(7),
                             "missing": ";".join(sorted({g for g in m.group(2, 4, 6) if is_code(g)})),
                             "page": pno, "row_as_printed": line})
            elif NOT_ROWS.match(line):
                if line.startswith(("Region", "CAR", "BARMM", "CARAGA", "Negros", "NIR")):
                    region = line
            else:
                raise ValueError(f"{period} p{pno}: unrecognised Appendix B line {line!r}")
    if len(rows) < 100:  # NEA assesses about 120 ECs; far fewer means a parsing failure, not fewer ECs
        raise ValueError(f"{period}: only {len(rows)} Appendix B rows")
    return rows


def utility_ids() -> dict[str, str]:
    u = pd.read_csv(DATA / "utilities.csv", dtype=str, keep_default_na=False)
    ids = {}
    for r in u.itertuples():
        for n in (r.doe_name, r.wiki_acronym):
            if n:
                ids.setdefault(acronym_key(n), r.utility_id)
    return ids


def fetch(period: str) -> tuple:
    path = SRC / f"{period}.pdf"
    if not path.exists():
        if OFFLINE:
            raise FileNotFoundError(path)
        r = httpx.get(f"https://drive.usercontent.google.com/download?id={REPORTS[period]}&export=download",
                      headers={"User-Agent": UA}, follow_redirects=True, timeout=120)
        r.raise_for_status()
        if not r.content.startswith(b"%PDF"):
            raise ValueError(f"{period}: Drive returned {r.headers.get('content-type')}, not a PDF")
        SRC.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def issues(df: pd.DataFrame) -> pd.DataFrame:
    """Year-to-date figures can't fall within a year; one that does is a question for NEA."""
    out = []
    df = df.assign(year=df.period.str[:4]).sort_values(["utility_id", "period"])
    for (uid, _), g in df.groupby(["utility_id", "year"]):
        for col in ("saifi", "saidi_minutes"):
            prev = None
            for r in g.itertuples():
                v = getattr(r, col)
                if prev is not None and v is not None and pd.notna(v) and v < prev[1]:
                    out.append({"utility_id": uid, "measure": col, "period": r.period, "value": v,
                                "previous_period": prev[0], "previous_value": prev[1],
                                "issue": "year-to-date figure lower than the previous quarter's"})
                if v is not None and pd.notna(v):
                    prev = (r.period, v)
    return pd.DataFrame(out, columns=["utility_id", "measure", "period", "value", "previous_period", "previous_value", "issue"])


def main() -> None:
    if OFFLINE and not all((SRC / f"{p}.pdf").exists() for p in REPORTS):  # PDFs are gitignored: a fresh clone has none
        print("NEA: reports not cached; keeping the committed data/outages/reliability_nea.csv", file=sys.stderr)
        return
    ids, rows = utility_ids(), []
    for period, fid in REPORTS.items():
        path, digest = fetch(period)
        for r in parse(path, period):
            uid = CROSSWALK.get(r["nea_name"]) or ids.get(acronym_key(r["nea_name"]), "")
            rows.append({"utility_id": uid, **r, "basis": "year to date (inferred)",
                         "source_url": f"https://drive.google.com/file/d/{fid}/view", "source_page": PAGE, "sha256": digest})
    df = pd.DataFrame(rows)
    missing = sorted(df[df.utility_id == ""].nea_name.unique())
    if missing:
        raise ValueError(f"NEA names with no utility_id: {missing}; add them to CROSSWALK")
    dup = df[df.duplicated(["utility_id", "period"], keep=False)]
    if len(dup):
        raise ValueError(f"two NEA rows for one utility and quarter: {dup[['nea_name', 'utility_id', 'period']].values.tolist()}")
    cols = ["utility_id", "nea_name", "region_as_printed", "period", "saifi", "saifi_status", "saidi_minutes", "saidi_status",
            "loss_pct", "loss_status", "missing", "basis", "source_url", "source_page", "page", "row_as_printed", "sha256"]
    df[cols].to_csv(DATA / "outages" / "reliability_nea.csv", index=False)
    iss = issues(df)
    iss.to_csv(DATA / "qa" / "nea_reliability_issues.csv", index=False)
    print(f"NEA: {len(df)} rows, {df.utility_id.nunique()} ECs, {df.period.nunique()} quarters; "
          f"{df.saifi.isna().sum()} without SAIFI (dns/inc); {len(iss)} year-to-date drops", file=sys.stderr)


if __name__ == "__main__":
    main()
