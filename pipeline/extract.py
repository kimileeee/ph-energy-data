"""Read a rate document of any layout (scanned PDF, image, infographic) into typed facts, then check them.

No per-site parsing rules. Three steps, each replaceable:
  1. OCR: every word on the page with its position (Apple Vision on macOS via pipeline/ocr.swift; Tesseract in CI).
  2. Read: a vision model fills one schema, FACTS_SCHEMA, from the image plus the OCR text: what was measured
     (generation charge, effective rate, a component...), for which class and month, as a level or a change, in
     which unit, at what consumption, and the value as printed. It is told never to compute.
  3. Check, deterministically, before anything is trusted:
     - grounding: the printed value must be found in the OCR text (which also gives its box for the evidence viewer);
     - arithmetic: components must add up to the stated total (a bill row, or a month-on-month change);
     - plausibility: rates within the same band the DOE sheet uses.
     A fact that fails grounding is kept but marked "model only"; a document whose arithmetic fails goes to review.

When a utility redesigns its notice, steps 1-3 run unchanged; only a failed check needs a person.

Model backends: the Anthropic API when ANTHROPIC_API_KEY is set, else the local `claude` CLI (Claude Code, headless).
Results are cached by the document's SHA-256, so a document is read once.
"""

import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import pdfplumber
from PIL import Image

from pipeline.doe import PLAUSIBLE
from pipeline.fetch import RAW

DATA = RAW.parent
CACHE = RAW / "sources" / "_extract"  # gitignored with the rest of the cached sources
OUT = DATA / "extracted"
OCR_SCRIPT = Path(__file__).with_name("ocr.swift")
MAX_PAGES = 4  # cost guard: rate notices are one or two pages; a long PDF is not a rate notice
MIN_OCR_WIDTH = 2400  # small web images OCR badly; upscale first
MODEL = os.environ.get("EXTRACT_MODEL", "claude-sonnet-5-5")

MEASURES = ["effective_rate", "generation_charge", "transmission_charge", "ancillary_charge", "system_loss_charge",
            "distribution_charge", "supply_charge", "metering_charge", "universal_charge", "fit_all", "gea_all",
            "taxes", "subsidies", "other_charges", "total_bill"]
FACTS_SCHEMA = {
    "type": "object",
    "properties": {
        "utility_name": {"type": "string"},
        "document_type": {"enum": ["typical_bills", "rate_schedule", "rate_change_notice", "generation_breakdown", "other"]},
        "facts": {"type": "array", "items": {"type": "object", "properties": {
            "measure": {"enum": MEASURES},
            "label_as_printed": {"type": "string"},
            "customer_class": {"enum": ["residential", "commercial", "industrial", "streetlights", "all", "other"]},
            "subclass": {"type": ["string", "null"]},
            "period": {"type": "string", "pattern": r"^\d{4}-\d{2}$"},
            "kind": {"enum": ["level", "change_from_previous_month"]},
            "unit": {"enum": ["PHP/kWh", "PHP/kW", "PHP/customer/month", "PHP", "percent"]},
            "consumption_kwh": {"type": ["number", "null"]},
            "value": {"type": "number"},
            "printed": {"type": "string"},
            "is_overall": {"type": "boolean"},
        }, "required": ["measure", "label_as_printed", "customer_class", "period", "kind", "unit", "value", "printed", "is_overall"]}},
    },
    "required": ["document_type", "facts"],
}

PROMPT = """You are reading a Philippine electricity distribution utility's published rate document.
Utility: {utility}. Source: {url}
The page that links this document files it under: {period_hint}. Use the billing month the document itself states;
if it states none, use that.

Read the image at: {image}
OCR of the same image (text with position, may contain errors):
{ocr}

Extract every rate figure printed on the document as a fact, following the JSON schema.
Rules:
- Copy numbers exactly as printed into "printed" (keep commas, parentheses, signs). In "value", parentheses mean negative.
- Never compute, add, or infer a number that is not printed. If a figure is not on the page, leave it out.
- "kind" is "change_from_previous_month" when the document states an increase/decrease versus the previous month.
- "period" is the billing month the figure applies to (YYYY-MM).
- Use measure "effective_rate" only for an all-in rate (or its change) per kWh for a customer class, not a single component.
- Map printed component names to the closest measure (e.g. "UC-ME", "UC-SD", "Missionary" -> universal_charge; "SL" -> system_loss_charge).
- "is_overall" is true when the figure is what the utility charges that class (e.g. its generation charge, or a total),
  and false when it is one supplier's cost, one plant's, or a sub-item that feeds into another printed figure.
"""


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def page_images(doc: Path, workdir: Path) -> list[Path]:
    """PDF pages rendered to PNG, or the image itself, upscaled if small."""
    if doc.suffix.lower() == ".pdf":
        out = []
        with pdfplumber.open(doc) as pdf:
            if len(pdf.pages) > MAX_PAGES:
                raise ValueError(f"{doc.name}: {len(pdf.pages)} pages; not a rate notice")
            for i, page in enumerate(pdf.pages, 1):
                p = workdir / f"{doc.stem}-p{i}.png"
                page.to_image(resolution=200).original.convert("RGB").save(p)
                out.append(p)
        return out
    img = Image.open(doc).convert("RGB")
    p = workdir / f"{doc.stem}.png"
    if img.width < MIN_OCR_WIDTH:
        k = MIN_OCR_WIDTH / img.width
        img = img.resize((MIN_OCR_WIDTH, round(img.height * k)), Image.LANCZOS)
    img.save(p)
    return [p]


def ocr(image: Path) -> list[dict]:
    r = subprocess.run(["swift", str(OCR_SCRIPT), str(image)], capture_output=True, text=True, timeout=300)
    if r.returncode:
        raise RuntimeError(f"OCR failed: {r.stderr[-400:]}")
    return json.loads(r.stdout)


def read_with_model(image: Path, lines: list[dict], context: dict) -> dict:
    ocr_text = "\n".join(f"[{l['box'][0]:.2f},{l['box'][1]:.2f}] {l['text']}" for l in lines)
    prompt = PROMPT.format(image=image, ocr=ocr_text, **context)
    if os.environ.get("ANTHROPIC_API_KEY"):
        import base64

        import anthropic  # optional dependency for CI

        msg = anthropic.Anthropic().messages.create(
            model=MODEL, max_tokens=8000,
            tools=[{"name": "record_facts", "description": "Record the extracted facts", "input_schema": FACTS_SCHEMA}],
            tool_choice={"type": "tool", "name": "record_facts"},
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                             "data": base64.b64encode(image.read_bytes()).decode()}},
                {"type": "text", "text": prompt.replace(f"Read the image at: {image}", "The image is attached.")}]}])
        return next(b.input for b in msg.content if b.type == "tool_use")
    r = subprocess.run(
        ["claude", "-p", prompt, "--output-format", "json", "--json-schema", json.dumps(FACTS_SCHEMA),
         "--model", MODEL, "--allowedTools", "Read", "--add-dir", str(image.parent)],
        capture_output=True, text=True, timeout=600, cwd=image.parent)
    if r.returncode:
        raise RuntimeError(f"claude -p failed: {r.stderr[-400:] or r.stdout[-400:]}")
    out = json.loads(r.stdout)
    result = out.get("structured_output") or out.get("result")
    return result if isinstance(result, dict) else json.loads(re.search(r"\{.*\}", result, re.S).group(0))


# ---------- checks ----------

def digits(s: str) -> str:
    return re.sub(r"[^\d.]", "", s)


def ground(fact: dict, lines: list[dict]) -> dict | None:
    """The OCR token holding this printed value, if any: its text and box."""
    want = digits(fact["printed"]).lstrip("0") or "0"
    for l in lines:
        for tok in re.split(r"[\s|]+", l["text"]):
            if digits(tok).lstrip("0") == want and want.strip("."):
                return {"ocr_text": l["text"], "box": l["box"]}
    return None


def check_sums(facts: list[dict]) -> list[dict]:
    """Components must add up to the stated all-in figure, per (class, period, kind, consumption)."""
    out = []
    groups: dict[tuple, list[dict]] = {}
    for f in facts:
        groups.setdefault((f["customer_class"], f.get("subclass"), f["period"], f["kind"], f.get("consumption_kwh"), f["unit"]), []).append(f)
    for key, fs in groups.items():
        total = [f for f in fs if f["measure"] in ("effective_rate", "total_bill")]
        parts = [f for f in fs if f["measure"] not in ("effective_rate", "total_bill")]
        if len(total) == 1 and len(parts) >= 2:
            s = round(sum(f["value"] for f in parts), 4)
            tol = 0.0005 * len(parts) + (0.011 if total[0]["unit"] == "PHP" else 0)
            out.append({"check": "components add up to the total", "group": list(map(str, key)),
                        "total": total[0]["value"], "sum_of_components": s, "n_components": len(parts),
                        "ok": abs(s - total[0]["value"]) <= tol})
    return out


def check_plausible(facts: list[dict]) -> list[dict]:
    return [{"check": "level within plausible band", "fact": f["label_as_printed"], "value": f["value"],
             "ok": PLAUSIBLE[0] <= f["value"] <= PLAUSIBLE[1]}
            for f in facts if f["kind"] == "level" and f["unit"] == "PHP/kWh" and f.get("is_overall")
            and f["measure"] in ("effective_rate", "generation_charge")]


def extract(doc: Path, utility: str, period_hint: str, url: str) -> dict:
    """Facts + checks for one document; cached by content hash."""
    # the cache key includes the prompt and schema, so changing either re-reads every document
    key = sha(doc)
    version = hashlib.sha256((PROMPT + json.dumps(FACTS_SCHEMA) + MODEL).encode()).hexdigest()[:8]
    cached = CACHE / f"{key}-{version}.json"
    if cached.exists():
        return json.loads(cached.read_text())
    with tempfile.TemporaryDirectory() as tmp:
        result = {"document": str(doc.relative_to(RAW / "sources")), "sha256": key, "source_url": url, "utility": utility, "pages": []}
        for pno, img in enumerate(page_images(doc, Path(tmp)), 1):
            lines = ocr(img)
            read = read_with_model(img, lines, {"utility": utility, "period_hint": period_hint, "url": url})
            facts = []
            for f in read.get("facts", []):
                g = ground(f, lines)
                facts.append({**f, "page": pno, "grounded": g is not None, **({"box": g["box"], "ocr_text": g["ocr_text"]} if g else {})})
            result["pages"].append({"page": pno, "document_type": read.get("document_type"), "ocr_lines": len(lines), "facts": facts})
        all_facts = [f for p in result["pages"] for f in p["facts"]]
        result["checks"] = check_sums(all_facts) + check_plausible(all_facts)
        result["grounded_share"] = round(sum(f["grounded"] for f in all_facts) / max(1, len(all_facts)), 3)
        result["status"] = ("review" if not all_facts or any(not c["ok"] for c in result["checks"]) or result["grounded_share"] < 0.8
                            else "ok")
        result["engine"] = {"ocr": "apple-vision", "model": MODEL,
                            "backend": "anthropic-api" if os.environ.get("ANTHROPIC_API_KEY") else "claude-cli"}
    CACHE.mkdir(parents=True, exist_ok=True)
    for old in CACHE.glob(f"{key}-*.json"):  # reads made with an older prompt or schema
        old.unlink()
    cached.write_text(json.dumps(result, indent=1))
    return result


if __name__ == "__main__":
    import sys

    doc, utility, period, url = sys.argv[1:5]
    print(json.dumps(extract(Path(doc).resolve(), utility, period, url), indent=1)[:6000])
