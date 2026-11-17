"""Power outages, first slice: planned-interruption notices -> one row per interruption window, and one row per
(window, barangay) with the barangay's 10-digit PSGC code.

Sources (docs/outages-findings.md covers what else exists and why it isn't read here):
  meralco  www.meralco.com.ph outages centre: the current maintenance schedule and the archive (server-rendered
           HTML, 6.6k notices since 2018). Text, so a parser that raises when the layout changes.
  aec      angeleselectric.com.ph WordPress API, category "System Advisories": one PDF per notice, some text and
           some scanned. Layout-free: OCR + vision model (pipeline/extract.py) into OUTAGE_SCHEMA, then checks.

Barangays are matched only inside the utility's franchise municipalities (data/utility_areas.csv), and a
municipality printed next to a barangay is binding. A name that fits no candidate, or more than one, stays
unmatched with its text; nothing is guessed. A notice that names a city missing from the franchise list is
logged to data/qa/outage_places_outside_coverage.csv: the notice is evidence the coverage source is short.

Outputs: data/outages/interruptions.csv, data/outages/interruption_areas.csv,
         data/qa/outage_places_outside_coverage.csv, data/qa/outage_notices_unread.csv (a read that failed: re-run)
Run:     uv run python -m pipeline.outages [--offline]
"""

import csv
import hashlib
import json
import re
import sys
import tempfile
import time
import unicodedata
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pandas as pd
from bs4 import BeautifulSoup

from pipeline.extract import MODEL, ocr, page_images, read_with_model, sha
from pipeline.fetch import RAW, UA

DATA = RAW.parent
OUT = DATA / "outages"
CACHE = RAW / "sources" / "outages"  # gitignored with the other cached sources
OFFLINE = "--offline" in sys.argv
SINCE = "2026-01-01"  # first slice: this year's notices (Meralco: parsed text, free to re-run)
AEC_SINCE = "2026-09-01"  # AEC: each notice is a model read, so the slice starts later

MERALCO = "https://www.meralco.com.ph/residential/outages-center/scheduled-service-maintenance"
AEC_API = "https://angeleselectric.com.ph/wp-json/wp/v2/posts"
AEC_SYSTEM_ADVISORIES = 5  # WordPress category id of "System Advisories"


# ---------- fetching (polite, cached) ----------

def get(url: str, refresh: bool = False) -> bytes:
    """URL body, cached on disk. Listing pages pass refresh=True; notices and PDFs don't change once posted."""
    path = CACHE / "http" / hashlib.sha256(url.encode()).hexdigest()[:24]
    if path.exists() and (OFFLINE or not refresh):
        return path.read_bytes()
    if OFFLINE:
        raise FileNotFoundError(f"not cached: {url}")
    time.sleep(0.5)
    r = httpx.get(url, headers={"User-Agent": UA}, follow_redirects=True, timeout=60)
    r.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(r.content)
    return r.content


# ---------- places ----------

ABBR = {"sta": "santa", "sto": "santo", "gen": "general", "pres": "presidente", "mt": "mount", "sr": "senor",
        # PSGC writes 'Amaya I', 'Garita I-A'; notices often 'Amaya 1', 'Garita 1-A'
        **{r: str(i) for i, r in enumerate(["i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x"], 1)}}
DROP = {"city", "of", "municipality", "bgy", "bgys", "brgy", "brgys", "barangay", "barangays", "pob", "poblacion"}


def key(s: str) -> str:
    """Place-name key: 'Bgy. Sto. Niño' == 'Santo Niño', 'Gen. Trias City' == 'City of General Trias',
    'Amaya 1' == 'Amaya I', 'Barangay 12' == '12' (Manila's numbered barangays). Parentheticals are dropped."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\(.*?\)", " ", s)
    words = [ABBR.get(w, w) for w in re.findall(r"[a-z0-9]+", s)]
    return "".join(w for w in words if w not in DROP)


OUTSIDE = "outside:"


class Places:
    """PSGC lookups for one utility: its franchise cities/municipalities and their barangays."""

    def __init__(self, utility_id: str):
        places = {r["psgc_id"]: r for r in csv.DictReader((RAW / "psgc" / "places.csv").open())}
        areas = pd.read_csv(DATA / "utility_areas.csv", dtype=str, keep_default_na=False)
        self.cities = {c: places[c]["name"] for c in areas[areas.utility_id == utility_id].psgc_code}
        self.city_by_key = {key(n): c for c, n in self.cities.items()}
        # every city/municipality name in the country, to notice a named place outside the franchise list
        self.province_keys = {key(p["name"]) for p in places.values() if p["type"] == "province"}
        self.any_city = {key(p["name"]): p["name"] for p in places.values()
                         if p["type"] in ("municipality", "component_city", "highly_urbanized_city", "independent_component_city")}
        self.brgys: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))  # city -> key -> rows
        for r in csv.DictReader((RAW / "psgc" / "barangays.csv").open()):
            city = r["parent_psgc_id"]
            while city in places and places[city]["type"] == "submunicipality":  # Manila's districts
                city = places[city]["parent_psgc_id"]
            if city in self.cities:
                self.brgys[city][key(r["name"])].append(r)

    def cities_named(self, text: str, towns_in_brackets: bool = False) -> tuple[list[str], list[str]]:
        """(franchise city codes named in the text, names of other cities/municipalities named outside brackets).
        Pass only a notice's title and location: its prose is full of street names (Cebu St., Lisbon St.).
        Brackets hold towns ('Bulacan (Angat; Bustos)') or barangays ('Quezon City (Holy Spirit)'), and many
        barangays share a town's name, so brackets count as towns only when the location is a province."""
        words = lambda t: " ".join(key(w) for w in re.split(r"[\s,;.()]+|\band\b", t) if key(w))
        inside = [c for ck, c in self.city_by_key.items() if ck and _has(words(text), ck)]
        bare = words(text if towns_in_brackets else re.sub(r"\(.*?\)", " ", text))
        outside = [n for ck, n in self.any_city.items()  # 'Aguinaldo' inside 'Gen. Emilio Aguinaldo' is not a place
                   if ck not in self.province_keys and not any(ck in fk for fk in self.city_by_key) and _has(bare, ck)]
        return inside, outside

    def match(self, name: str, hint: str | None, scope: list[str]) -> dict:
        """One barangay name -> its PSGC row if exactly one candidate fits. hint (a city code) is binding."""
        if hint and hint.startswith(OUTSIDE):
            return {"match": "not found", "match_scope": f"printed with {hint[len(OUTSIDE):]}, outside the franchise list",
                    "psgc_code": "", "psgc_name": "", "city_psgc_code": "", "city_name": ""}
        cities = [hint] if hint else scope or list(self.cities)
        cands = [r for c in cities for r in self.brgys[c].get(key(name), [])]
        how = "city printed with it" if hint else ("cities named in the notice" if scope else "whole franchise area")
        if len(cands) == 1:
            r = cands[0]
            city = self.parent_city(r)
            return {"match": "matched", "match_scope": how, "psgc_code": r["psgc_id"], "psgc_name": r["name"],
                    "city_psgc_code": city, "city_name": self.cities[city]}
        return {"match": "ambiguous" if cands else "not found", "match_scope": how, "psgc_code": "", "psgc_name": "",
                "city_psgc_code": hint or "", "city_name": self.cities.get(hint, "")}

    def parent_city(self, row: dict) -> str:
        return next(c for c in self.cities if row in self.brgys[c].get(key(row["name"]), []))


def _has(haystack: str, needle: str) -> bool:
    """needle (a key) appears as a run of whole words in the space-joined key sequence."""
    words = haystack.split()
    for i in range(len(words)):
        acc = ""
        for w in words[i:i + 6]:
            acc += w
            if acc == needle:
                return True
            if not needle.startswith(acc):
                break
    return False


MARK = re.compile(r"\b(?:Bgys?|Brgys?|Barangays?)\b\.?\s+(?!Hall\b|Health\b|Road\b|Captain\b|Council\b)", re.I)
# words that end a barangay list in Meralco's prose ("in Bgy. X including ...", "Bgy. X to Calcutta St.")
STOP = re.compile(r";|\(|\)|:|\b(?:in|including|from|to|along|until|up|near|corner|cor|and\s+portions?|portions?)\b", re.I)
SUFFIX = re.compile(r"^(?:[IVX]+(?:-[A-Z])?|\d+(?:-[A-Z])?|[A-Z])$")  # 'II', 'I-D', '9', 'B': the rest of a series


ABBREV = {"sta", "sto", "gen", "pres", "mt", "sr", "st", "sts", "ave", "subd", "subds", "cor", "dr", "jr", "blk", "no", "ph", "phs"}


def sentence_end(rest: str, end: int) -> int:
    """First '. X' in rest[:end] that ends a sentence, not an abbreviation ('Sto. Niño', 'B. F. Homes')."""
    for d in re.finditer(r"\.\s+(?=[A-Z])", rest[:end]):
        word = (re.findall(r"\w+$", rest[:d.start()]) or [""])[0]
        initials = len(word) == 1 and word.isalpha() and (
            re.match(r"[A-Z]\.", rest[d.end():]) or re.search(r"\b[A-Z]\.\s*[A-Z]$", rest[:d.start()]))
        if word and word.lower() not in ABBREV and not initials:
            return d.start()
    return end


def list_items(chunk: str) -> list[str]:
    """'Bucandala I, II, III & IV' -> each in full; 'Bayan Luma 7 – 8' -> 7 and 8."""
    items = []
    for raw in (i.strip(" .") for i in re.split(r",|\band\b|&", chunk)):
        if m := re.fullmatch(r"(.*?)(\d+)\s*[–-]\s*(\d+)", raw):
            items += [f"{m.group(1)}{n}" for n in range(int(m.group(2)), int(m.group(3)) + 1)][:10]
        elif raw and SUFFIX.match(raw):
            if items and len(items[-1].split()) > 1:  # a bare 'IV' with nothing to attach to is dropped
                items.append(items[-1].rsplit(" ", 1)[0] + " " + raw)
        elif raw:
            items.append(raw)
    return items


def split_city(item: str, places: "Places") -> tuple[str, str | None]:
    """'Bagumbayan Taguig City' -> ('Bagumbayan', Taguig's code)."""
    words = item.split()
    for i in range(1, len(words)):
        if "city" in item.lower() and (c := places.city_by_key.get(key(" ".join(words[i:])))):
            return " ".join(words[:i]), c
    return item, None


def barangay_mentions(text: str, places: "Places") -> list[tuple[str, str | None, str]]:
    """(barangay as printed, city code printed with it or None, the text it came from) for each 'Bgy./Bgys.' list.
    A city closes the list ('Bgys. A and B, Imus City'; 'in Imus City' right after); a town name in the middle of
    a list is read as a barangay of that name, since many barangays share town names (San Mateo, Dasmariñas)."""
    out = []
    for m in MARK.finditer(text):
        rest = text[m.end():]
        s = STOP.search(rest)
        end = sentence_end(rest, s.start() if s else len(rest))
        hint = None
        if m2 := re.match(r"\s*,?\s*in\s+([A-Z][^,;.()]+)", rest[end:]):
            hint = places.city_by_key.get(key(m2.group(1)))
        snippet = text[max(0, m.start() - 40): m.end() + end + 40].strip()
        items = [i for i in list_items(rest[:end]) if key(i) not in places.province_keys and "province" not in i.lower()]
        found = []
        for i, item in reversed(list(enumerate(items))):  # a city applies to the barangays listed before it
            if i > 0 and ("city" in item.lower() or i == len(items) - 1):
                if c := places.city_by_key.get(key(item)):
                    hint = c
                    continue
                if "city" in item.lower() and key(item) in places.any_city:  # outside the franchise list: nothing before it may match
                    hint = OUTSIDE + places.any_city[key(item)]
                    continue
            name, h = split_city(item, places)
            if len(name.split()) <= 6:
                found.append((name, h or hint, snippet))
        out += found[::-1]
    return out


# ---------- time windows ----------

T = r"(\d{1,2}(?::\d{2})?)\s*(A\.?M\.?|P\.?M\.?|NN|NOON|MN|MIDNIGHT)"
D = r"(?:\s*\((?:\w+\.?,?\s*)?(\d{1,2}/\d{1,2}/\d{2,4})\))?"
WINDOW = re.compile(rf"BETWEEN\s+{T}{D}\s+AND\s+{T}{D}", re.I)


def clock(hm: str, ampm: str) -> tuple[int, int]:
    h, _, m = hm.partition(":")
    h, m, ap = int(h), int(m or 0), ampm.upper().replace(".", "")
    if ap in ("NN", "NOON"):
        return 12, m
    if ap in ("MN", "MIDNIGHT"):
        return 0, m
    return (h % 12 + (12 if ap == "PM" else 0)), m


def window(text: str, default_date: str) -> tuple[datetime, datetime]:
    """'BETWEEN 11:00PM (WED., 06/10/26) AND 4:00AM (THU., 06/11/26)' or 'BETWEEN 9:00AM AND 2:00PM' + date."""
    m = WINDOW.search(text)
    if not m:
        raise ValueError(f"no 'BETWEEN ... AND ...' window in {text[:120]!r}")

    def day(s):
        if not s:
            return datetime.fromisoformat(default_date)
        mo, d, y = s.split("/")
        return datetime(int(y) + (2000 if len(y) == 2 else 0), int(mo), int(d))

    d1 = day(m.group(3))
    d2 = day(m.group(6)) if m.group(6) else d1
    start = d1.replace(hour=clock(*m.group(1, 2))[0], minute=clock(*m.group(1, 2))[1])
    end = d2.replace(hour=clock(*m.group(4, 5))[0], minute=clock(*m.group(4, 5))[1])
    if end <= start and not m.group(6):  # '10:00PM AND 4:00AM' runs past midnight
        end += timedelta(days=1)
    if not timedelta(0) < end - start <= timedelta(hours=48):
        raise ValueError(f"implausible window {start} -> {end} in {text[:120]!r}")
    return start, end


# ---------- Meralco ----------

def meralco_notices() -> list[dict]:
    """Current schedule cards + this year's archive notices, each with its windows (circuit + areas text)."""
    notices = []
    soup = BeautifulSoup(get(MERALCO, refresh=True), "html.parser")
    cards = soup.select("article.maintenance-activity[data-source-id]")
    if not cards and "maintenance schedules" not in soup.get_text():
        raise ValueError("Meralco schedule page: no cards and no count line; layout changed?")
    for a in cards:
        dates = a["data-filter-dates"].split("|")
        windows = []
        for sec in a.select("section.maintenance-activity__area"):
            head = sec.select_one(".maintenance-activity__area-heading p").get_text(" ", strip=True)
            windows.append({"when": head, "circuit": re.sub(r"^.*?CIRCUITS?\s+", "", text_of(sec.select_one(".maintenance-activity__circuit"))),
                            "areas": text_of(sec.select_one(".maintenance-activity__paragraphs"))})
        notices.append({"notice_id": a["data-source-id"], "title": text_of(a.select_one("h4")),
                        "location": text_of(a.select_one(".maintenance-activity__meta")), "date": dates[0],
                        "n_dates": len(dates), "reason": text_of(a.select_one(".maintenance-activity__reason p")),
                        "source_url": f"{MERALCO}#{a['id']}", "windows": windows})
    year = SINCE[:4]
    links, page = [], 1
    while True:
        url = f"{MERALCO}/archive" + (f"/page-{page}" if page > 1 else "") + f"?year={year}"
        s = BeautifulSoup(get(url, refresh=True), "html.parser")
        found = [a["href"] for a in s.select("a[href*='/scheduled-service-maintenance/archive/v2-']")]
        if not found:
            break
        links += [h for h in found if h not in links]
        if not s.find(string=re.compile(rf"Page {page} of")) or s.find(string=re.compile(rf"Page {page} of {page}\b")):
            break
        page += 1
    if not links:
        raise ValueError(f"Meralco archive {year}: no notice links; layout changed?")
    for href in links:
        url = "https://www.meralco.com.ph" + href if href.startswith("/") else href
        d = BeautifulSoup(get(url), "html.parser")
        facts = {dt.get_text(strip=True): dt.find_next_sibling("dd") for dt in d.select(".maintenance-archive-detail__metadata dt")}
        if "Date" not in facts:
            raise ValueError(f"{url}: no Date fact; layout changed?")
        prose = d.select_one("#MaintenanceArchiveDetail-notice").find_next_sibling("div")
        windows, cur = [], None
        for el in prose.descendants:  # areas run from one bold window line to the next
            if getattr(el, "name", None) == "strong":
                cur = {"when": el.get_text(" ", strip=True), "areas": ""}
                windows.append(cur)
            elif isinstance(el, str) and cur is not None and el.parent.name != "strong":
                cur["areas"] += el
        for w in windows:
            w["areas"] = re.sub(r"\s+", " ", w["areas"]).strip()
            c = re.search(r"CIRCUITS?\s+(.+)$", w["when"])
            w["circuit"] = c.group(1) if c else ""
        if not windows:
            raise ValueError(f"{url}: no window lines; layout changed?")
        reason = d.select_one("#MaintenanceArchiveDetail-reason")
        start = datetime.strptime(facts["Date"].get_text(strip=True).split("–")[0].strip(), "%d %B %Y").date().isoformat()
        notices.append({"notice_id": href.rsplit("/", 1)[-1], "title": text_of(d.select_one("h1")),
                        "location": text_of(facts.get("Location")), "date": start, "n_dates": 1,
                        "reason": text_of(reason.find_next_sibling("div")) if reason else "", "source_url": url, "windows": windows})
    return [n for n in notices if n["date"] >= SINCE]


def text_of(el) -> str:
    return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else ""


def meralco() -> tuple[list[dict], list[dict], list[dict]]:
    places = Places("meralco")
    rows, areas, outside = [], [], []
    for n in meralco_notices():
        named, other = places.cities_named(f"{n['title']} ; {n['location']}", towns_in_brackets=key(n["location"]) in places.province_keys)
        outside += [{"utility_id": "meralco", "place_as_printed": o, "notice": n["source_url"], "title": n["title"]} for o in other]
        for i, w in enumerate(n["windows"], 1):
            start, end = window(w["when"], n["date"])
            iid = f"meralco-{n['notice_id']}-{i}"
            note = "card spans several dates; window printed without its date" if n["n_dates"] > 1 and "/" not in w["when"] else ""
            rows.append({"interruption_id": iid, "utility_id": "meralco", "notice_id": n["notice_id"], "window": i,
                         "start": start.isoformat() + "+08:00", "end": end.isoformat() + "+08:00",
                         "hours": round((end - start).total_seconds() / 3600, 2), "kind": "scheduled",
                         "reason": n["reason"], "reason_category": "", "circuit": w["circuit"], "title": n["title"],
                         "window_as_printed": w["when"], "areas_as_printed": w["areas"], "source_url": n["source_url"],
                         "document_url": "", "document_sha256": "", "read_by": "html parser", "status": "review" if note else "ok",
                         "checks": note or "window parsed; dates from the notice"})
            seen = set()
            for name, hint, snippet in barangay_mentions(w["areas"] + " " + n["reason"], places):
                m = places.match(name, hint, named)
                if (m["psgc_code"] or name) in seen:
                    continue
                seen.add(m["psgc_code"] or name)
                areas.append({"interruption_id": iid, "utility_id": "meralco", "barangay_as_printed": name, "detail": "",
                              **m, "evidence": snippet})
    return rows, areas, outside


# ---------- AEC (layout-free reader) ----------

OUTAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "notice_type": {"enum": ["scheduled", "emergency", "unscheduled", "cancelled", "rescheduled", "not_an_interruption"]},
        "windows": {"type": "array", "items": {"type": "object", "properties": {
            "date": {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
            "start": {"type": "string", "pattern": r"^\d{2}:\d{2}$"},
            "end": {"type": "string", "pattern": r"^\d{2}:\d{2}$"},
            "end_date": {"type": ["string", "null"], "pattern": r"^\d{4}-\d{2}-\d{2}$"},
            "date_printed": {"type": "string"},
            "time_printed": {"type": "string"},
        }, "required": ["date", "start", "end", "date_printed", "time_printed"]}},
        "areas": {"type": "array", "items": {"type": "object", "properties": {
            "barangay_printed": {"type": "string"},
            "municipality_printed": {"type": ["string", "null"]},
            "detail": {"type": ["string", "null"]},
        }, "required": ["barangay_printed"]}},
        "areas_printed": {"type": "string"},
        "reason_printed": {"type": ["string", "null"]},
        "reason_category": {"enum": ["maintenance", "rehabilitation_or_upgrade", "relocation", "new_connection",
                                    "emergency_repair", "supplier_or_transmission", "other", "not_stated"]},
    },
    "required": ["notice_type", "windows", "areas", "reason_category"],
}

OUTAGE_PROMPT = """You are reading a Philippine electricity distribution utility's power-interruption notice.
Utility: {utility}. Source: {url}
The utility posted it on {posted} with the title "{title}".

Read the image at: {image}
OCR of the same image (text with position, may contain errors):
{ocr}

Fill the JSON schema from what the notice prints.
Rules:
- Never infer anything that is not printed. Copy the date and the time range exactly as printed into date_printed and time_printed.
- One entry in "windows" per interruption period. Times are 24-hour HH:MM. If a period runs past midnight, end_date is the next day; otherwise null.
- "areas": one entry per barangay named among the affected areas: the barangay name as printed without "Brgy."/"Barangay",
  the city or municipality only if printed with it, and the sitio, subdivision, street or establishments named for it as "detail".
- If the notice is not about a power interruption (rates, payments, hiring...), notice_type is "not_an_interruption" and windows and areas are empty.
"""
VERSION = hashlib.sha256((OUTAGE_PROMPT + json.dumps(OUTAGE_SCHEMA) + MODEL).encode()).hexdigest()[:8]


def aec_posts() -> list[dict]:
    posts, page = [], 1
    while True:
        url = (f"{AEC_API}?categories={AEC_SYSTEM_ADVISORIES}&after={AEC_SINCE}T00:00:00&per_page=100&page={page}"
               "&_fields=id,date,link,title,content")
        batch = json.loads(get(url, refresh=True))
        posts += batch
        if len(batch) < 100:
            return posts
        page += 1


def aec_document(post: dict) -> str:
    """The notice file: the embedded PDF, else the first uploaded image."""
    html = post["content"]["rendered"]
    m = re.search(r'href="([^"]+\.pdf)"', html) or re.search(r'src="([^"]+/wp-content/uploads/[^"]+\.(?:png|jpe?g))"', html)
    if not m:
        raise ValueError(f"AEC post {post['link']}: no PDF or image in its content")
    return m.group(1)


def read_notice(post: dict) -> dict:
    """OCR + model read of one AEC notice, cached by the file's SHA-256 and the prompt/schema version."""
    url = aec_document(post)
    body = get(url)
    doc = CACHE / "aec" / Path(urlparse(url).path).name
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_bytes(body)
    cached = CACHE / "_read" / f"{sha(doc)}-{VERSION}.json"
    if cached.exists():
        return json.loads(cached.read_text())
    if OFFLINE:
        raise FileNotFoundError(f"not read yet: {url}")
    with tempfile.TemporaryDirectory() as tmp:
        img = page_images(doc, Path(tmp))[0]  # notices are one page
        lines = ocr(img)
        read = read_with_model(img, lines, {"utility": "Angeles Electric Corporation", "url": url, "posted": post["date"][:10],
                                            "title": BeautifulSoup(post["title"]["rendered"], "html.parser").get_text()},
                               prompt=OUTAGE_PROMPT, schema=OUTAGE_SCHEMA)
    result = {"document_url": url, "sha256": sha(doc), "lines": lines, "read": read}
    cached.parent.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(result, indent=1))
    return result


def check_notice(read: dict, lines: list[dict], title: str) -> list[str]:
    """Deterministic checks; each failure is a reason for review."""
    ocr_key = key(" ".join(l["text"] for l in lines))
    ocr_digits = re.sub(r"\D", "", " ".join(l["text"] for l in lines))
    problems = []
    for a in read.get("areas", []):
        if key(a["barangay_printed"]) not in ocr_key:
            problems.append(f"barangay '{a['barangay_printed']}' not in the OCR text")
    for w in read.get("windows", []):
        if re.sub(r"\D", "", w["date_printed"]) and re.sub(r"\D", "", w["date_printed"])[-4:] not in ocr_digits:
            problems.append(f"date '{w['date_printed']}' not in the OCR text")
        if not w.get("end_date") and w["end"] <= w["start"]:
            problems.append(f"window {w['start']}-{w['end']} ends before it starts")
    if m := re.match(r"(\d{1,2})-(\d{1,2})-(\d{4})", title):  # AEC titles start with the date: '11-18-2026 Cutcut'
        title_date = f"{m.group(3)}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
        if read.get("windows") and title_date not in {w["date"] for w in read["windows"]}:
            problems.append(f"title date {title_date} is not a window date")
    if read.get("notice_type") != "not_an_interruption" and not read.get("windows"):
        problems.append("no window read")
    return problems


def evidence(name: str, lines: list[dict]) -> str:
    for l in lines:
        if key(name) and key(name) in key(l["text"]):
            return f"{l['text']} @ {[round(b, 3) for b in l['box']]}"
    return ""


def aec() -> tuple[list[dict], list[dict], list[dict]]:
    places = Places("aec")
    posts = aec_posts()
    def safe(post):
        try:
            return read_notice(post)
        except (RuntimeError, FileNotFoundError, ValueError) as e:  # model quota, offline, unreadable file
            msg = str(e)
            return {"error": "model usage limit reached" if "limit" in msg else msg.splitlines()[0][:160]}

    with ThreadPoolExecutor(4) as pool:
        reads = list(pool.map(safe, posts))
    unread = [{"utility_id": "aec", "source_url": p["link"], "posted": p["date"][:10], "error": r["error"]}
              for p, r in zip(posts, reads) if "error" in r]
    pd.DataFrame(unread, columns=["utility_id", "source_url", "posted", "error"]).to_csv(
        DATA / "qa" / "outage_notices_unread.csv", index=False)  # re-run to read them; never silently dropped
    rows, areas, outside = [], [], []
    for post, r in zip(posts, reads):
        if "error" in r:
            continue
        read, title = r["read"], BeautifulSoup(post["title"]["rendered"], "html.parser").get_text()
        if read["notice_type"] == "not_an_interruption":
            continue
        problems = check_notice(read, r["lines"], title)
        for i, w in enumerate(read["windows"], 1):
            start = datetime.fromisoformat(f"{w['date']}T{w['start']}")
            end = datetime.fromisoformat(f"{w.get('end_date') or w['date']}T{w['end']}")
            iid = f"aec-{post['id']}-{i}"
            rows.append({"interruption_id": iid, "utility_id": "aec", "notice_id": str(post["id"]), "window": i,
                         "start": start.isoformat() + "+08:00", "end": end.isoformat() + "+08:00",
                         "hours": round((end - start).total_seconds() / 3600, 2), "kind": read["notice_type"],
                         "reason": read.get("reason_printed") or "", "reason_category": read["reason_category"], "circuit": "",
                         "title": title, "window_as_printed": f"{w['date_printed']} {w['time_printed']}",
                         "areas_as_printed": read.get("areas_printed", ""), "source_url": post["link"],
                         "document_url": r["document_url"], "document_sha256": r["sha256"], "read_by": f"ocr + {MODEL}",
                         "status": "review" if problems else "ok", "checks": "; ".join(problems) or "all checks passed"})
            for a in read["areas"]:
                town = a.get("municipality_printed") or ""
                hint = places.city_by_key.get(key(town))
                if town and not hint:  # a printed town outside the franchise list is binding too
                    hint = OUTSIDE + town
                    outside.append({"utility_id": "aec", "place_as_printed": town, "notice": post["link"], "title": title})
                areas.append({"interruption_id": iid, "utility_id": "aec", "barangay_as_printed": a["barangay_printed"],
                              "detail": a.get("detail") or "", **places.match(a["barangay_printed"], hint, []),
                              "evidence": evidence(a["barangay_printed"], r["lines"])})
    return rows, areas, outside


def main() -> None:
    OUT.mkdir(exist_ok=True)
    rows, areas, outside = [], [], []
    for source in (meralco, aec):
        r, a, o = source()
        rows, areas, outside = rows + r, areas + a, outside + o
        print(f"{source.__name__}: {len({x['notice_id'] for x in r})} notices, {len(r)} windows, "
              f"{sum(x['match'] == 'matched' for x in a)}/{len(a)} barangays matched", file=sys.stderr)
    pd.DataFrame(rows).sort_values(["start", "interruption_id"]).to_csv(OUT / "interruptions.csv", index=False)
    pd.DataFrame(areas).to_csv(OUT / "interruption_areas.csv", index=False)
    pd.DataFrame(outside, columns=["utility_id", "place_as_printed", "notice", "title"]).drop_duplicates(
        ["utility_id", "place_as_printed", "notice"]).to_csv(DATA / "qa" / "outage_places_outside_coverage.csv", index=False)


if __name__ == "__main__":
    main()
