"""Map data: municipal boundaries as GeoJSON for MapLibre, joined to the utilities that serve them.

Input:  data/raw/geo/geojson-medres.zip (faeldon/philippines-json-maps, PSA PSGC 3Q 2026, 1% of vertices),
        data/utility_areas.csv, data/raw/psgc/places.csv
Output: site/geo/municipalities.geojson
        properties: c (10-digit PSGC), n (name), p (province), u (JSON: [[utility_id, qualifier], ...]),
        b (bounding box [w, s, e, n], for "zoom to this place")

BetterGov's open-data-visualization repo ships the same faeldon low-resolution file (same 1,642 shapes and
27,240 points); medium resolution is used here because the map now sits on a street basemap and zooms in close.
"""

import csv
import json
import zipfile

from pipeline.fetch import RAW

DATA = RAW.parent
OUT = DATA.parent / "site" / "geo" / "municipalities.geojson"
SOURCE = RAW / "geo" / "geojson-medres.zip"
LEVEL = "/provdists/medres/"
DECIMALS = 4  # ~11 m: finer than the 1%-vertex boundaries themselves


def rnd(coords):
    """Round coordinates and drop points that collapse onto the previous one."""
    if isinstance(coords[0], (int, float)):
        return [round(coords[0], DECIMALS), round(coords[1], DECIMALS)]
    out = [rnd(c) for c in coords]
    if out and isinstance(out[0][0], float):  # a ring
        ring = [p for i, p in enumerate(out) if i == 0 or p != out[i - 1]]
        return ring if len(ring) >= 4 else out
    return out


def bbox(geom) -> list[float]:
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    xs = [p[0] for poly in polys for ring in poly for p in ring]
    ys = [p[1] for poly in polys for ring in poly for p in ring]
    return [round(min(xs), 4), round(min(ys), 4), round(max(xs), 4), round(max(ys), 4)]


def main() -> None:
    places = {r["psgc_id"]: r for r in csv.DictReader((RAW / "psgc" / "places.csv").open())}
    served: dict[str, list[list[str]]] = {}
    for a in csv.DictReader((DATA / "utility_areas.csv").open()):
        served.setdefault(a["psgc_code"], []).append([a["utility_id"], a["qualifier"]])

    z = zipfile.ZipFile(SOURCE)
    features = []
    for name in z.namelist():
        if LEVEL not in name or not name.endswith(".json"):
            continue
        for f in json.loads(z.read(name))["features"]:
            code = str(f["properties"]["adm3_psgc"]).zfill(10)
            prov = places.get(str(f["properties"]["adm2_psgc"]).zfill(10), {})
            geom = {"type": f["geometry"]["type"], "coordinates": rnd(f["geometry"]["coordinates"])}
            features.append({"type": "Feature", "geometry": geom, "properties": {
                "c": code, "n": places.get(code, {}).get("name") or f["properties"]["adm3_en"],
                "p": prov.get("name", "") if prov.get("type") == "province" else "",
                "u": json.dumps(served.get(code, []), ensure_ascii=False), "b": bbox(geom)}})

    OUT.parent.mkdir(parents=True, exist_ok=True)
    (OUT.parent / "municipalities.json").unlink(missing_ok=True)  # the old pre-projected SVG file
    OUT.write_text(json.dumps({"type": "FeatureCollection", "features": features}, separators=(",", ":"), ensure_ascii=False))
    with_u = sum(1 for f in features if f["properties"]["u"] != "[]")
    print(f"map: {len(features)} municipalities/cities ({with_u} with a utility), {OUT.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
