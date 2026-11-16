# CLAUDE.md

Prototype dataset + static site for Philippine electricity rates per distribution utility (BetterGov Fellowship 2026, Energy track). Read `PROGRESS.md` first for status; `README.md` for what each file is.

## Commands

```bash
uv run python -m pipeline              # full run (network: DOE sheet, PSGC, Wikipedia, boundaries, Meralco, CELCOR, TEI)
uv run python -m pipeline --offline    # rebuild from data/raw + last data/qa
uv run python -m pipeline.<step>       # fetch | doe | coverage | geo | verify | publish | report | site
uv run python -m pipeline.test_pipeline
python3 -m http.server 8765 --directory site
```

Always use absolute paths in Bash.

## Rules that are easy to break

- **Never change DOE's numbers.** `data/rates.csv` holds DOE's values as printed. Problems are flagged in `data/qa/`, never fixed in place. Out-of-band values (outside 1–40 PHP/kWh) stay in the CSV and are only left out of medians and charts.
- **IDs are issued once.** `doe.py` reads the committed `data/utilities.csv` to keep existing `utility_id`s. Don't regenerate IDs by deleting that file.
- **Don't invent coverage.** PSGC codes come only from areas the coverage source lists. A province hint in the source ("Samal, Davao del Norte") is binding; if no candidate fits, the area goes to `qa/unmatched_areas.csv`, never to a best guess.
- **Every scraper raises on a layout change** (e.g. "no 200 kWh row") rather than returning nothing. Keep it that way; silent zeros would look like matches or gaps.
- **Site:** no third-party requests (no CDN, fonts, analytics), except the map's OpenFreeMap tiles, which Kim asked for and which have an off switch. Libraries are vendored under `site/vendor/` with their version in the path. Chart colors are the dataviz reference palette's first three slots (validated all-pairs); keep light/dark tokens in `:root`.
- **Privacy:** nothing about the author beyond the name "Kim Williame Lee" — no email, phone or address anywhere in the repo or site.
- **`site/methodology.html` is generated.** Edit `pipeline/templates/methodology.html`; every figure is a `{{placeholder}}` filled by `pipeline/site.py`, and an unknown placeholder fails the build. Write sentences that stay true when the numbers change.
- **Explorer state lives only in the URL hash** (`du`, `class`, `view`, `month`). Controls call `go()`; don't add state that isn't in the hash.
- **Map colours** are five fixed bands (`BINS` in `app.js`) on the validated sequential ramp (`--seq-1..5`, reversed for dark). No-data is hatched, never coloured as low.
- **No per-layout parsing for images.** Image/scan sources go through `pipeline/extract.py` (OCR → vision model → one schema → deterministic checks). A new utility needs only discovery (where its documents are), never layout rules. Don't trust a model fact that isn't grounded in OCR without saying so; failed arithmetic means review, not data.
- **Facebook:** no logged-in or headless scraping (Meta's automated-collection terms). Use the inbox (`data/inbox/<utility_id>/`) until a sanctioned route exists.
- **Every source figure keeps its evidence.** A scraper returns the value *and* where it sits (`locate()` in `verify.py`: page, row text, row and value boxes). A new scraper without evidence doesn't belong on the checks page. When DOE's method is unknown, say so; a mismatch is a question for DOE, not a verdict.
- **The checks viewer follows the Naga portal's source viewer:** docked beside the table, not a modal; Esc closes; j/k walk rows. Keep it that way.
- **`openapi.json` is generated** (`pipeline/openapi.py`). Add an endpoint there, not by hand, and lint with `npx @redocly/cli@1.34.3 lint site/openapi.json`.
- **PSGC codes are strings** everywhere (leading zeros). `publish.py` reads code columns with `dtype=str`; keep it so for any new code column.
- **Don't hand-edit `?v=` stamps** on script/style links; `pipeline/site.py` rewrites them each build.
- Raw utility PDFs are gitignored; `data/raw/sources/manifest.json` keeps URL + sha256.
- Ask Kim before creating a public GitHub repo or deploying.
