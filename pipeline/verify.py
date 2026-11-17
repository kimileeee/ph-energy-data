"""Check DOE's hand-entered numbers against what utilities publish themselves.

Scrapers read each utility's own monthly rate document for the last LOOKBACK months:
  meralco  rates archive page -> "Residential Bills at Typical Consumption Levels" PDF:
           residential = printed PHP/kWh at 200 kWh (non-lifeline), generation = printed generation charge
  celcor   JSON API behind its archive page (api.celcor.com.ph) -> residential-bills PDF:
           residential = total bill at 200 kWh / 200, generation = generation charge at 200 kWh / 200
  tei      "Rate Schedule of <Month> <Year>" page -> embedded PDF: printed generation charge (residential column)
Every figure keeps its evidence: the document, page, the row's text and where the row and the value sit on
the page, so the checks page can show the source with the number highlighted.
Plus a reachability audit of every link in DOE's SOURCES column.

Outputs: data/qa/discrepancies.csv, data/qa/source_links.csv, data/raw/sources/manifest.json,
         site/sources/** (mirrored PDFs), site/evidence/*.webp (page images)
PDFs are cached under data/raw/sources/ (gitignored); the manifest keeps URL + sha256.
"""

import hashlib
import io
import json
import re
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from urllib.parse import urljoin, urlparse

import httpx
import pandas as pd
import pdfplumber
from PIL import Image

from pipeline.fetch import RAW

DATA = RAW.parent
SITE = DATA.parent / "site"
SRC = RAW / "sources"
BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36"
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"]
# DOE prints two decimals; anything further apart than rounding can explain is a discrepancy
TOLERANCE = 0.006
LOOKBACK = 12  # months
RENDER_DPI = 150  # page images for the viewer; Meralco's tables use ~6pt type

# Where each utility publishes, for the checks page and the methodology.
SOURCES = {
    "meralco": {"name": "Meralco", "archive": "https://www.meralco.com.ph/governance/regulatory/rates-archive",
                "how": "Rates archive page lists each month's PDFs; the residential-bills table prints PHP/kWh per consumption level."},
    "celcor": {"name": "CELCOR", "archive": "https://www.celcor.com.ph/news-and-advisories/rate-archives",
               "api": "https://api.celcor.com.ph/Home/ratesarchives?limit=200",
               "how": "The archive page is a JavaScript app; its data comes from a JSON API, found by watching the page's network "
                      "requests. The residential-bills PDF prints the total bill per consumption level."},
    "tei": {"name": "Tarlac Electric (TEI)", "archive": "https://tarlacelectric.com/rate-schedule-customer-class",
            "how": "One page per month embeds the month's rate schedule PDF, which prints each charge per customer class."},
    "aec": {"name": "AEC (Angeles Electric)", "archive": "https://angeleselectric.com.ph/advisories/",
            "how": "Monthly bill insert: one image (also posted on Facebook) giving the change in the residential rate and its "
                   "components. Read from the image by OCR and a vision model; checked against the change in DOE's figures."},
    "cedc": {"name": "CEDC (Clark Electric)", "archive": "https://www.clarkelectric.ph/bills-and-payment/generation-charges-and-bill-rates",
             "how": "Rate schedule and generation-cost tables published as images. Read by OCR and a vision model; the generation "
                    "charge is compared directly, and the schedule's charges before tax set a floor DOE's all-in rate must clear."},
}
# Tried and not automated yet: shown on the checks page and in the report.
NOT_AUTOMATED = [
    {"utility_id": "", "name": "54 utilities linked to Facebook", "url": "",
     "why": "Facebook pages can't be read without logging in, and Meta's terms forbid automated collection without its permission. "
            "Until a sanctioned route exists (Graph API Page Public Content Access, utilities connecting their Pages, or a DOE filing), "
            "a person saves the month's rate post into data/inbox/<utility_id>/ and the same image reader takes it from there."},
]

manifest: dict[str, dict] = {}


def client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": BROWSER_UA}, follow_redirects=True, timeout=60)


def fetch(c: httpx.Client, url: str, cache_name: str) -> bytes:
    path = SRC / cache_name
    if not path.exists():
        r = c.get(url)
        r.raise_for_status()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(r.content)
    body = path.read_bytes()
    manifest[cache_name] = {"url": url, "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}
    return body


def pdf_text(body: bytes) -> str:
    with pdfplumber.open(io.BytesIO(body)) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)


def recent_months(today: date, n: int) -> list[str]:
    y, m = today.year, today.month
    out = []
    for _ in range(n):
        out.append(f"{y}-{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out


def num(s: str) -> float:
    s = s.replace(",", "")
    return -float(s[1:-1]) if s.startswith("(") else float(s)


def rejoin(line: str) -> list[str]:
    """Tokens of a table row. Some PDFs carry stray spaces inside numbers ("1 3.1145", "0 .3350", "( 0.0002)"):
    a lone digit or "(" is always the start of the next number in these tables."""
    return re.sub(r"(?:(?<=\s)|^)([\d(])\s+(?=[\d.,(])", r"\1", line).split()


# ---------- parsers: text in, (values, how to find them on the page) out ----------

def meralco_bills(text: str) -> dict | None:
    """Meralco residential-bills PDF: the 200 kWh row of the PHP/kWh table (4-decimal numbers)."""
    if "RESIDENTIAL BILLS AT TYPICAL" not in text.upper():
        return None
    levels, found = {}, None  # kWh -> PHP/kWh, to say which consumption level DOE's figure corresponds to
    for line in text.splitlines():
        toks = rejoin(line)
        if toks and toks[0].isdigit() and int(toks[0]) > 0 and len(toks) > 3 and re.fullmatch(r"\d+\.\d{4}", toks[-1]):
            levels[int(toks[0])] = num(toks[-1])
            if toks[0] == "200" and not found:
                found = {"residential": num(toks[-1]), "generation": num(toks[1]),
                         "_find": {"residential": (toks[-1], "last"), "generation": (toks[1], "first")}}
    if not found:
        raise ValueError("residential bills PDF without a 200 kWh PHP/kWh row; layout changed?")
    return found | {"_levels": levels}


def celcor_bills(text: str) -> dict:
    """CELCOR residential-bills PDF: the 200 kWh row prints pesos; divide by 200 for PHP/kWh."""
    if "RESIDENTIAL BILLS AT TYPICAL" not in text.upper():
        raise ValueError("CELCOR file is not a residential-bills table; layout changed?")
    levels = {}  # kWh -> total bill per kWh, to say which consumption level (if any) DOE's figure corresponds to
    found = None
    for line in text.splitlines():
        toks = rejoin(line)
        if toks and toks[0].isdigit() and int(toks[0]) > 0 and len(toks) > 5:
            levels[int(toks[0])] = round(num(toks[-1]) / int(toks[0]), 4)
        if toks[:1] == ["200"] and len(toks) > 5:
            found = {"residential": round(num(toks[-1]) / 200, 4), "generation": round(num(toks[1]) / 200, 4),
                     "_find": {"residential": (toks[-1], "last"), "generation": (toks[1], "first")}}
    if not found:
        raise ValueError("CELCOR residential bills PDF without a 200 kWh row; layout changed?")
    return found | {"_levels": levels}


def tei_generation(text: str) -> float:
    m = re.search(r"Generation Charge\d*\s+PhP/kWh\s+([\d.,()]+)", text)
    if not m:
        raise ValueError("TEI rate schedule without a Generation Charge row; layout changed?")
    return num(m.group(1))


# ---------- evidence: where a value sits on its page ----------

def locate(body: bytes, row_pred, raw: str, which: str) -> dict:
    """Page number, the row's text, and the row and value boxes as fractions of the page (x0, top, x1, bottom)."""
    key = raw.replace(" ", "")
    with pdfplumber.open(io.BytesIO(body)) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            for line in page.extract_text_lines():
                if not row_pred(line["text"]):
                    continue
                chars = [c for c in line["chars"] if c["text"].strip()]
                s = "".join(c["text"] for c in chars)
                # "first" skips the row label (e.g. the leading "200") so a value can't match inside it
                i = s.rfind(key) if which == "last" else s.find(key, 3)
                if i < 0:
                    continue
                span = chars[i:i + len(key)]
                W, H = float(page.width), float(page.height)
                box = lambda o: ",".join(f"{v:.4f}" for v in (o["x0"] / W, o["top"] / H, o["x1"] / W, o["bottom"] / H))
                vbox = {"x0": min(c["x0"] for c in span), "top": min(c["top"] for c in span),
                        "x1": max(c["x1"] for c in span), "bottom": max(c["bottom"] for c in span)}
                return {"page": pno, "row_text": re.sub(r"\s+", " ", line["text"]).strip(), "row_box": box(line), "value_box": box(vbox)}
    raise ValueError(f"could not find {raw!r} on any page")


def starts_200(text: str) -> bool:
    return text.strip().startswith("200 ")


def is_gen_row(text: str) -> bool:
    return bool(re.search(r"Generation Charge\d*\s+PhP/kWh", text))


# ---------- scrapers ----------

def scrape_meralco(months: list[str]) -> list[dict]:
    base = SOURCES["meralco"]["archive"]
    out = []
    with client() as c:
        html = fetch(c, base, "meralco/rates-archive.html").decode("utf-8", "ignore")
        links: dict[str, list[str]] = {}
        for href, label in re.findall(r'href="([^"]+\.pdf[^"]*)"[^>]*>\s*(?:<[^>]+>\s*)*([A-Z][a-z]+ 20\d\d)', html):
            mon, yr = label.split()
            key = f"{yr}-{MONTHS.index(mon.lower()) + 1:02d}"
            if key in months and href not in links.setdefault(key, []):
                links[key].append(href)
        for key, hrefs in sorted(links.items()):
            # filenames usually say which document it is; hashed names have to be opened
            hrefs.sort(key=lambda h: 0 if "residential" in h.lower() else 1 if "source-delta" in h or "legacy" in h else 2)
            for href in hrefs:
                if re.search(r"gc-table|rate-schedule", href, re.I):
                    continue
                url = urljoin(base, href)
                doc = f"meralco/{key}-{urlparse(url).path.rsplit('/', 1)[-1]}"
                body = fetch(c, url, doc)
                try:
                    got = meralco_bills(pdf_text(body))
                except ValueError as e:  # layout changed: read it like any other document instead of failing
                    print(f"meralco {key}: {e}; reading it as an image")
                    out += from_extracted("meralco", doc, url, base, "Meralco residential bills", [key])
                    break
                if got:
                    levels = got.pop("_levels")
                    for measure, (raw, which) in got.pop("_find").items():
                        out.append({"levels": levels if measure == "residential" else None, "utility_id": "meralco", "month": key, "measure": measure, "source_value": got[measure],
                                    "source_url": url, "doc": doc, "source_page_url": base,
                                    "source_document": f"Residential Bills at Typical Consumption Levels, {key}",
                                    "method": ("Printed: PHP/kWh at 200 kWh, non-lifeline (Total column)" if measure == "residential"
                                               else "Printed: generation charge, PHP/kWh, 200 kWh row"),
                                    **locate(body, starts_200, raw, which)})
                    break
    return out


def scrape_celcor(months: list[str]) -> list[dict]:
    out = []
    with client() as c:
        listing = c.get(SOURCES["celcor"]["api"], headers={"Origin": "https://www.celcor.com.ph"}).json()
        for item in listing:
            key = item["BillMonth"][:7]
            if item["Category"] != "Residential Bill at Typical Consumption" or key not in months:
                continue
            url = "https://restapi.celcor.com.ph/files/rates/" + item["FileName"].strip()
            doc = f"celcor/{key}-residential-bills.pdf"
            body = fetch(c, url, doc)
            try:
                got = celcor_bills(pdf_text(body))
            except ValueError as e:
                print(f"celcor {key}: {e}; reading it as an image")
                out += from_extracted("celcor", doc, url, SOURCES["celcor"]["archive"], "CELCOR residential bills", [key])
                continue
            levels = got.pop("_levels")
            for measure, (raw, which) in got.pop("_find").items():
                out.append({"levels": levels if measure == "residential" else None, "utility_id": "celcor", "month": key, "measure": measure, "source_value": got[measure],
                            "source_url": url, "doc": doc, "source_page_url": SOURCES["celcor"]["archive"],
                            "source_document": f"Residential Bills at Typical Consumption Levels, {key}",
                            "method": (f"Computed: total bill at 200 kWh (₱{raw}) ÷ 200" if measure == "residential"
                                       else f"Computed: generation charge at 200 kWh (₱{raw}) ÷ 200"),
                            **locate(body, starts_200, raw, which)})
    return out


def scrape_tei(months: list[str]) -> list[dict]:
    out = []
    with client() as c:
        for key in months:
            y, m = key.split("-")
            page = f"https://tarlacelectric.com/rate-schedule-of-{MONTHS[int(m) - 1]}-{y}"
            r = c.get(page)
            if r.status_code != 200:
                continue
            pdfs = re.findall(r'href="([^"]+\.pdf)"', r.text)
            if not pdfs:
                continue
            doc = f"tei/{key}.pdf"
            body = fetch(c, pdfs[0], doc)
            try:
                value = tei_generation(pdf_text(body))
            except ValueError as e:
                print(f"tei {key}: {e}; reading it as an image")
                out += from_extracted("tei", doc, pdfs[0], page, "TEI rate schedule", [key])
                continue
            raw = re.search(r"Generation Charge\d*\s+PhP/kWh\s+([\d.,()]+)", pdf_text(body)).group(1)
            out.append({"utility_id": "tei", "month": key, "measure": "generation", "source_value": value,
                        "source_url": pdfs[0], "doc": doc, "source_page_url": page,
                        "source_document": f"Rate Schedule for the month of {MONTHS[int(m) - 1].title()} {y}",
                        "method": "Printed: Generation Charge, Residential column",
                        **locate(body, is_gen_row, raw, "first")})
    return out


# ---------- any layout: OCR + vision model + checks (pipeline/extract.py) ----------

MEASURE_TO_DOE = {"effective_rate": "residential", "generation_charge": "generation"}


def from_extracted(utility_id: str, doc: str, url: str, page_url: str, title: str, months: list[str],
                   period_hint: str = "") -> list[dict]:
    """Comparable rows from whatever the document turns out to contain. Nothing here knows the layout."""
    from pipeline import billcalc
    from pipeline.extract import extract

    name = SOURCES.get(utility_id, {}).get("name", utility_id)
    try:
        x = extract(SRC / doc, name, period_hint or "no month given", url)
    except ValueError as e:  # not a rate document (too long, unreadable): skip it, don't stop the run
        print(f"skipped {doc}: {e}")
        return []
    facts = [f for p in x["pages"] for f in p["facts"]]
    base = {"utility_id": utility_id, "doc": doc, "source_url": url, "source_page_url": page_url,
            "read_by": "image", "doc_status": x["status"]}
    out = []
    for f in facts:
        if f["period"] not in months or not f.get("is_overall") or f["customer_class"] not in ("residential", "all"):
            continue
        measure = MEASURE_TO_DOE.get(f["measure"])
        if not measure or f["unit"] != "PHP/kWh" or (f.get("subclass") or "").strip():
            continue
        out.append({**base, "month": f["period"], "measure": measure, "source_value": f["value"],
                    "check_type": "change" if f["kind"] == "change_from_previous_month" else "same",
                    "source_document": f"{title} ({f['period']})",
                    "method": (f"Read from image: \"{f['label_as_printed']}\" printed {f['printed']}"
                               + ("" if f["grounded"] else " (not confirmed by OCR)")),
                    "page": f["page"], "row_text": f.get("ocr_text") or f["label_as_printed"],
                    "row_box": box_str(f.get("box")), "value_box": box_str(f.get("box")), "grounded": f["grounded"]})
    # a rate schedule without totals: the formula gives the residential charges before tax, a floor for DOE's figure
    for period in sorted({f["period"] for f in facts if f["period"] in months}):
        b = billcalc.from_facts(facts, period)
        if b and len(b["items"]) >= 5:
            out.append({**base, "month": period, "measure": "residential", "source_value": b["rate_before_tax"],
                        "check_type": "floor", "source_document": f"{title} ({period})",
                        "method": f"Computed: the schedule's residential charges at {b['kwh']} kWh, before VAT and local taxes "
                                  f"(₱{b['subtotal']:,.2f} ÷ {b['kwh']})",
                        "breakdown": json.dumps(b), "page": 1, "row_text": "", "row_box": "", "value_box": "", "grounded": True})
    return out


def box_str(b) -> str:
    return ",".join(f"{v:.4f}" for v in b) if b else ""


def scrape_aec(months: list[str]) -> list[dict]:
    """Discovery only: PDFs on AEC's billing-advisory posts. What each one says comes from the reader."""
    base = SOURCES["aec"]["archive"]
    out = []
    with client() as c:
        listing = c.get(base).text
        posts = sorted(set(re.findall(r'href="(https://angeleselectric\.com\.ph/[a-z0-9-]*(?:rates?|billing)[a-z0-9-]*/)"', listing)))
        pdfs = set()
        for post in posts:
            pdfs |= set(re.findall(r'(https://angeleselectric\.com\.ph/wp-content/uploads/\d{4}/\d{2}/[^"\s]+\.pdf)', c.get(post).text))
        # only files uploaded in the months we check (the sidebar links years-old corporate reports on every post)
        recent = {m.replace("-", "/") for m in months}
        for url in sorted(u for u in pdfs if re.search(r"/uploads/(\d{4}/\d{2})/", u).group(1) in recent):
            doc = f"aec/{urlparse(url).path.rsplit('/', 1)[-1]}"
            fetch(c, url, doc)
            out += from_extracted("aec", doc, url, base, "AEC bill insert", months,
                                  period_hint=re.search(r"/uploads/(\d{4})/(\d{2})/", url).expand(r"\1-\2 (upload month)"))
    return out


def scrape_cedc(months: list[str]) -> list[dict]:
    """Discovery only: CEDC's rates page lists each month under a "<Month> <Year>" heading followed by its images.
    The tables themselves print no month, so the heading is the month the reader is told."""
    base = SOURCES["cedc"]["archive"]
    todo = []
    with client() as c:
        page = c.get(base).text
        current, seen = None, set()
        for m in re.finditer(r'((?:%s)\s+20\d\d)|src="(/sites/default/files/inline-images/[^"]+)"' % "|".join(x.title() for x in MONTHS), page):
            if m.group(1):
                mon, yr = m.group(1).split()
                current = f"{yr}-{MONTHS.index(mon.lower()) + 1:02d}"
                continue
            src = m.group(2)
            if current not in months or src in seen:
                continue
            seen.add(src)
            url = urljoin(base, src)
            doc = f"cedc/{current}-{src.rsplit('/', 1)[-1]}"
            fetch(c, url, doc)
            todo.append((doc, url, current))
    # ponytail: 4 parallel reads; the model backend is the bottleneck, raise if the API's rate limit allows
    with ThreadPoolExecutor(4) as pool:
        parts = pool.map(lambda t: from_extracted("cedc", t[0], t[1], base, "CEDC rate image", months, period_hint=t[2]), todo)
    return [row for part in parts for row in part]


def scrape_inbox(months: list[str]) -> list[dict]:
    """Files a person saved by hand (e.g. a Facebook rate post): data/inbox/<utility_id>/<anything>.{png,jpg,pdf}.
    Same reader, same checks; the source link is the post URL if a .url file of the same name holds it."""
    inbox = DATA / "inbox"
    out = []
    for f in sorted(inbox.glob("*/*")) if inbox.exists() else []:
        if f.suffix.lower() not in (".png", ".jpg", ".jpeg", ".pdf"):
            continue
        doc = f"inbox/{f.parent.name}/{f.name}"
        (SRC / doc).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(f, SRC / doc)
        body = (SRC / doc).read_bytes()
        link = f.with_suffix(".url").read_text().strip() if f.with_suffix(".url").exists() else ""
        manifest[doc] = {"url": link or "saved by hand", "sha256": hashlib.sha256(body).hexdigest(), "bytes": len(body)}
        out += from_extracted(f.parent.name, doc, link, link, "Saved rate post", months)
    return out


def drop_mislabels(found: list[dict]) -> list[dict]:
    """An all-in rate always exceeds the generation charge it contains. A "residential rate" equal to (or below) the same
    month's generation charge is a mislabelled generation figure (e.g. a generation-cost table's TOTAL row)."""
    gen = {(f["utility_id"], f["month"]): f["source_value"] for f in found
           if f["measure"] == "generation" and f.get("check_type", "same") == "same"}
    keep = []
    for f in found:
        g = gen.get((f["utility_id"], f["month"]))
        if f["measure"] == "residential" and f.get("check_type", "same") == "same" and g is not None and f["source_value"] <= g + 0.0001:
            print(f"dropped {f['utility_id']} {f['month']}: 'residential' {f['source_value']} is not above the generation charge {g}")
            continue
        keep.append(f)
    return keep


def flag_thin_breakdowns(found: list[dict]) -> list[dict]:
    """A utility's schedule lists the same charges every month. A month whose formula breakdown has fewer than the
    utility's fullest month means the reader missed some; the floor still holds (a missing charge only lowers it)."""
    full: dict[str, int] = {}
    for f in found:
        if f.get("check_type") == "floor":
            full[f["utility_id"]] = max(full.get(f["utility_id"], 0), len(json.loads(f["breakdown"])["items"]))
    for f in found:
        if f.get("check_type") == "floor":
            n = len(json.loads(f["breakdown"])["items"])
            if n < full[f["utility_id"]]:
                f["note"] = (f"This month's breakdown has {n} charges, against {full[f['utility_id']]} in the fullest month: "
                             f"the reader missed some, so the floor here is understated")
    return found


def dedupe(found: list[dict]) -> list[dict]:
    """One row per (utility, month, figure, check). The same figure printed in two documents must agree; if not, say so."""
    seen: dict[tuple, dict] = {}
    for f in sorted(found, key=lambda f: (not f.get("grounded", True), f["doc"])):
        k = (f["utility_id"], f["month"], f["measure"], f.get("check_type", "same"))
        if k not in seen:
            seen[k] = f
        elif abs(seen[k]["source_value"] - f["source_value"]) > 0.0001:
            seen[k]["conflict"] = f"{f['doc']} prints {f['source_value']}"
    return list(seen.values())


# ---------- comparison ----------

def compare(found: list[dict]) -> pd.DataFrame:
    """Three kinds of check: the same figure (same), the same month-on-month change (change), and a floor: DOE's all-in
    rate can't be below the utility's own charges before tax (floor)."""
    rates = pd.read_csv(DATA / "rates.csv", keep_default_na=False)
    res = rates[rates.customer_class == "residential"].set_index(["utility_id", "month"])
    months = sorted(rates.month.unique())
    rows = []
    for f in found:
        key = (f["utility_id"], f["month"])
        if key not in res.index:
            continue  # month not in DOE's sheet at all (e.g. not compiled yet)
        col, note = ("rate_php_per_kwh", "rate_note") if f["measure"] == "residential" else ("generation_charge", "generation_note")
        doe, doe_note = res.loc[key][col], res.loc[key][note]
        kind = f.get("check_type", "same")
        if kind == "change":
            i = months.index(f["month"])
            prev = res.loc[(f["utility_id"], months[i - 1])][col] if i and (f["utility_id"], months[i - 1]) in res.index else ""
            doe, doe_note = (round(float(doe) - float(prev), 2), "") if doe != "" and prev != "" else ("", doe_note or "previous month")
        explain = ""
        if doe == "":
            status, diff = f"doe_missing ({doe_note or 'blank'})", None
        else:
            diff = round(abs(float(doe) - f["source_value"]), 4)
            # a change is two rounded DOE figures subtracted, so it can be off by up to a centavo
            tol = {"same": TOLERANCE, "change": 0.011, "floor": TOLERANCE}[kind]
            status = ("match" if float(doe) >= f["source_value"] - tol else "mismatch") if kind == "floor" else \
                     ("match" if diff <= tol else "mismatch")
            if kind == "floor" and status == "mismatch":
                explain = "DOE's all-in figure is below the utility's own charges before VAT and local taxes"
            if status == "mismatch" and f.get("levels"):
                # does DOE's number match the bill at some other consumption level?
                kwh, v = min(f["levels"].items(), key=lambda kv: abs(kv[1] - float(doe)))
                hit = abs(v - float(doe)) <= TOLERANCE
                explain = (f"DOE's figure matches the {kwh} kWh bill ({v:.4f})" if hit else
                           f"No consumption level in the utility's table gives DOE's figure; closest is {kwh} kWh at {v:.4f}")
        if f.get("note"):
            explain = "; ".join(filter(None, [explain, f["note"]]))
        if f.get("conflict"):
            explain = "; ".join(filter(None, [explain, f"Another document disagrees: {f['conflict']}"]))
        rows.append({"read_by": "text", "check_type": "same", "grounded": True, "breakdown": "", "doc_status": "ok",
                     **{k: v for k, v in f.items() if k not in ("levels", "conflict", "note")}, "check_type": kind,
                     "doe_value": doe, "abs_diff": diff, "status": status, "explain": explain,
                     "sha256": manifest[f["doc"]]["sha256"]})
    cols = ["utility_id", "month", "measure", "check_type", "doe_value", "source_value", "abs_diff", "status", "explain",
            "method", "read_by", "grounded", "doc_status", "source_document", "source_url", "source_page_url", "doc", "sha256",
            "page", "row_text", "row_box", "value_box", "breakdown"]
    return pd.DataFrame(rows, columns=cols).sort_values(["utility_id", "measure", "month", "check_type"])


def publish_evidence(disc: pd.DataFrame) -> None:
    """Mirror each cited PDF and render each cited page once; the checks page draws the highlight over it."""
    for d in (SITE / "sources", SITE / "evidence"):
        shutil.rmtree(d, ignore_errors=True)
    for doc, page in disc[["doc", "page"]].drop_duplicates().itertuples(index=False):
        mirror = SITE / "sources" / doc
        mirror.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(SRC / doc, mirror)
        img = SITE / "evidence" / f"{evidence_stem(doc)}-p{int(page)}.webp"
        img.parent.mkdir(parents=True, exist_ok=True)
        if doc.lower().endswith(".pdf"):
            with pdfplumber.open(SRC / doc) as pdf:
                pdf.pages[int(page) - 1].to_image(resolution=RENDER_DPI).original.convert("RGB").save(img, "WEBP", quality=70)
        else:
            Image.open(SRC / doc).convert("RGB").save(img, "WEBP", quality=80)


def evidence_stem(doc: str) -> str:
    return re.sub(r"\.(pdf|png|jpe?g)$", "", doc, flags=re.I).replace("/", "-")


# ---------- DOE source-link audit ----------

def audit_links() -> pd.DataFrame:
    u = pd.read_csv(DATA / "utilities.csv", dtype=str, keep_default_na=False)

    def check(row):
        url = row["doe_source_url"]
        host = urlparse(url).netloc.lower()
        kind = "none" if not url else "facebook" if "facebook.com" in host else "pdf" if url.lower().endswith(".pdf") else "website"
        status = ""
        if kind in ("website", "pdf"):  # facebook refuses unauthenticated clients, so we don't pretend to check it
            try:
                with client() as c:
                    status = str(c.get(url, timeout=25).status_code)
            except httpx.HTTPError as e:
                status = type(e).__name__
        return {"utility_id": row["utility_id"], "doe_name": row["doe_name"], "doe_source_url": url,
                "doe_source_text": row["doe_source_text"], "kind": kind, "http_status": status}

    with ThreadPoolExecutor(12) as pool:
        return pd.DataFrame(pool.map(check, u.to_dict("records")))


def gap_months(utility_id: str) -> list[str]:
    """Months where DOE's sheet has no residential rate or no generation charge for this utility (NDA or blank)."""
    rates = pd.read_csv(DATA / "rates.csv", keep_default_na=False)
    r = rates[(rates.utility_id == utility_id) & (rates.customer_class == "residential")]
    return sorted(r[(r.rate_php_per_kwh == "") | (r.generation_charge == "")].month)


# utilities whose own archives reach back far enough to fill DOE's older gaps
FILL_FROM_ARCHIVE = ("meralco", "cedc")


def main() -> None:
    months = recent_months(date.today(), LOOKBACK)
    window = {u: sorted(set(months) | (set(gap_months(u)) if u in FILL_FROM_ARCHIVE else set())) for u in SOURCES}
    found = scrape_meralco(window["meralco"]) + scrape_celcor(months) + scrape_tei(months)
    found += scrape_aec(months) + scrape_cedc(window["cedc"]) + scrape_inbox(months)
    found = flag_thin_breakdowns(dedupe(drop_mislabels(found)))
    disc = compare(found)
    disc.to_csv(DATA / "qa" / "discrepancies.csv", index=False)
    filled = disc[disc.status.str.startswith("doe_missing") & (disc.check_type == "same")]
    filled.assign(doe_note=filled.status.str.extract(r"\((.*)\)")[0])[
        ["utility_id", "month", "measure", "source_value", "doe_note", "read_by", "method", "source_url", "doc", "page"]
    ].rename(columns={"source_value": "value_php_per_kwh"}).to_csv(DATA / "filled.csv", index=False)
    publish_evidence(disc)
    links = audit_links()
    links.to_csv(DATA / "qa" / "source_links.csv", index=False)
    (SRC / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    print(disc.groupby(["utility_id", "measure"]).status.value_counts().to_string())
    print(links.groupby("kind").http_status.value_counts().to_string())


if __name__ == "__main__":
    main()
