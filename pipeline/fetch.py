"""Download raw snapshots into data/raw/. Everything downstream reads only these files."""

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"
UA = "ph-energy-data/0.1 (open-data prototype)"

SHEET_ID = "1oGc1iGpacELS6tkHjesvAeKZo8v2iv4UkYFY04e0p-I"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"
# gid -> customer class. Order matters only for readability.
TABS = {"0": "residential", "441532959": "commercial", "1244383053": "industrial", "714337242": "generation"}

# PSA's PSGC, 2026 publication, as parsed by github.com/bendlikeabamboo/barangay (MIT).
PSGC_TAG = "v2026.10.12.0"
PSGC_URL = f"https://raw.githubusercontent.com/bendlikeabamboo/barangay/{PSGC_TAG}/barangay/data/barangay_flat.json"
# Municipal boundaries, PSGC 3Q 2026: github.com/faeldon/philippines-json-maps (MIT), low resolution (0.1% of vertices).
GEO_TAG = "v1.1.1"
GEO_URL = f"https://github.com/faeldon/philippines-json-maps/releases/download/{GEO_TAG}/geojson-lowres.zip"
WIKI_TITLE = "List_of_electric_distribution_utilities_in_the_Philippines"
SOURCES = {
    **{f"doe_{gid}.csv": f"{SHEET_URL}/export?format=csv&gid={gid}" for gid in TABS},
    # xlsx only for the SOURCES column hyperlinks, which CSV export drops.
    "doe_rates.xlsx": f"{SHEET_URL}/export?format=xlsx",
    "geo/geojson-lowres.zip": GEO_URL,
    "wikipedia/du_list.wiki": f"https://en.wikipedia.org/w/index.php?title={WIKI_TITLE}&action=raw",
}


def get(client: httpx.Client, url: str) -> bytes:
    r = client.get(url, follow_redirects=True, timeout=60)
    r.raise_for_status()
    return r.content


def write_barangays(psgc: list[dict]) -> int:
    """Barangays (42k rows) in their own file; only the outage notices need them."""
    rows = [p for p in psgc if p["type"] == "barangay"]
    with (RAW / "psgc" / "barangays.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, ["psgc_id", "name", "parent_psgc_id"], extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def main() -> None:
    manifest = {}
    with httpx.Client(headers={"User-Agent": UA}) as client:
        for name, url in SOURCES.items():
            path = RAW / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(get(client, url))
            manifest[name] = {"url": url, "bytes": path.stat().st_size}
            print(f"fetched {name} ({path.stat().st_size:,} B)", file=sys.stderr)
        # places.csv keeps everything above barangay level (~1.8k rows); barangays go to barangays.csv
        psgc = json.loads(get(client, PSGC_URL))
        places = [p for p in psgc if p["type"] != "barangay"]
        path = RAW / "psgc" / "places.csv"
        path.parent.mkdir(exist_ok=True)
        with path.open("w", newline="") as f:
            w = csv.DictWriter(f, ["psgc_id", "name", "type", "parent_psgc_id"], extrasaction="ignore")
            w.writeheader()
            w.writerows(places)
        manifest["psgc/places.csv"] = {"url": PSGC_URL, "rows": len(places)}
        manifest["psgc/barangays.csv"] = {"url": PSGC_URL, "rows": write_barangays(psgc)}
        rev = client.get(
            "https://en.wikipedia.org/w/api.php",
            params={"action": "query", "prop": "revisions", "rvprop": "ids|timestamp", "titles": WIKI_TITLE, "format": "json"},
        ).json()
        page = next(iter(rev["query"]["pages"].values()))["revisions"][0]
        manifest["wikipedia/du_list.wiki"]["revision"] = page
    manifest["_fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    (RAW / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
