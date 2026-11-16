# ph-energy-data

Philippine electricity rates per distribution utility as an open, joinable dataset: DOE's hand-compiled monthly rate sheet turned into a tidy table, a master list of utilities with stable IDs and PSGC codes, automated checks of DOE's numbers against the utilities' own rate documents, and a static "your utility, your price" page with a JSON API.

Prototype for the BetterGov Fellowship 2026 Energy track. Not an official DOE product.

## What's in it

| File | What it is |
|---|---|
| `data/rates.csv` / `.parquet` | Tidy long table: `utility_id, month, customer_class, rate_php_per_kwh, generation_charge`, plus `rate_note` (NDA / blank / unparsed) and DOE's source. 141 utilities × 107 months × 3 classes. |
| `data/utilities.csv` / `.parquet` | Master list v0. One row per utility: stable `utility_id`, name as DOE prints it, full name, type, grid, region (+ PSGC region code), island, PSGC province codes of the franchise area, independent cities served, DOE's source link, coverage note. |
| `data/utility_areas.csv` / `.parquet` | City/municipality-level franchise areas with PSGC codes and qualifiers ("Some areas only", "Off-the-grid"). |
| `data/datapackage.json` | Frictionless Data Package describing every file, with primary and foreign keys. |
| `data/qa/` | `discrepancies.csv` (DOE vs. utility documents), `doe_sheet_issues.csv`, `source_links.csv`, `unmatched_areas.csv`, `areas_outside_doe_region.csv`. |
| `reports/discrepancy-report.md` | Human-readable summary of the checks, regenerated each run. |
| `site/` | Static site, no dependencies or trackers: `index.html` + `app.js` (explorer with Chart / Map / Table tabs), `checks.html` + `checks.js` (every DOE-vs-source comparison with a docked viewer of the source page, value highlighted), `data.html` + `data.js` (downloads and a Swagger-style API reference read from `openapi.json`), `methodology.html` (generated), `style.css`. Data under `site/api/`, map geometry in `site/geo/`, mirrored source PDFs in `site/sources/`, page images in `site/evidence/`. |

### API (static JSON)

- `/api/utilities.json`: master list with each utility's latest rate per class
- `/api/rates/<utility_id>.json`: monthly series per class and generation charge, NDA counts, values flagged by QA
- `/api/panel.json`: every utility's monthly series in one file, aligned to a shared month list (drives the map and the all-utilities table)
- `/api/medians.json`: national and regional medians per month and class (unweighted)
- `/api/checks.json`: every DOE-vs-source comparison, with DOE's cell link, the source document, page and value location
- `/api/catalog.json`: the bulk files with row counts and sizes
- `/openapi.json`: OpenAPI 3.1 description of all of the above (lints clean with Redocly)
- `/api/stats.json`: coverage numbers

## Run it

Python 3.12 with [uv](https://docs.astral.sh/uv/). `pdfplumber` reads the PDFs; nothing else outside the Python deps.

```bash
uv sync
uv run python -m pipeline              # fetch → parse → coverage → map → check sources → publish → report → methodology page
uv run python -m pipeline --offline    # rebuild from data/raw and the last checks, no network
uv run python -m pipeline.test_pipeline
python3 -m http.server 8765 --directory site   # then open http://localhost:8765
```

Steps, each runnable on its own as `python -m pipeline.<step>`:

1. `fetch`: snapshots DOE's four tabs (CSV, plus the XLSX for the hyperlinks in the SOURCES column), PSGC, and the Wikipedia coverage table into `data/raw/`, with `manifest.json`.
2. `doe`: parses the wide sheet (title rows, region sub-headers, `NDA`) into the long table; issues IDs; flags problems.
3. `coverage`: matches each utility's listed areas to PSGC and rolls them up to provinces.
4. `verify`: reads Meralco, CELCOR (through the JSON API behind its archive page) and Tarlac Electric rate PDFs for the last 12 months, compares them with DOE's entries, and keeps evidence for each figure (page, row text, row and value boxes); mirrors the PDFs and renders the cited pages; audits DOE's source links.
5. `geo`: municipal boundaries (faeldon medium resolution) as GeoJSON, joined to the utilities that serve each place (`site/geo/municipalities.geojson`).
6. `publish`: Parquet, datapackage, JSON API, stats, `checks.json`, `openapi.json`, `catalog.json`.
7. `report`: writes `reports/discrepancy-report.md`.
8. `site`: renders `site/methodology.html` from `pipeline/templates/methodology.html`, filling every figure from the build, and stamps each page's JS/CSS links with a content hash.

The explorer keeps its whole view in the URL hash (`#du=meralco&class=residential&view=map&month=2026-10`), so any view can be shared as a link. The layout follows the Naga budget portal's data explorer research (Chart / Map / Table tabs after Our World in Data's Grapher, with a table as the accessible fallback) and its methodology page (live figures inside prose that stays true).

## Reading any document (images, scans, Facebook posts)

`pipeline/extract.py` reads a rate document of any layout without per-site rules: OCR (`pipeline/ocr.swift`, Apple Vision; Tesseract on Linux) → a vision model fills one fixed schema of facts (measure, class, month, level or change, unit, kWh basis, value as printed) → deterministic checks decide what is trusted (the printed value must be in the OCR text, components must add up, levels must be plausible, the same figure in two documents must agree). Failures go to review, not into the data. Results are cached by the file's SHA-256 plus a hash of the prompt and schema.

- Model backend: the Anthropic API when `ANTHROPIC_API_KEY` is set (`EXTRACT_MODEL`, default `claude-sonnet-5-5`), otherwise the local `claude` CLI in headless mode.
- AEC (bill-insert images) and CEDC (rate-table images) go through it; the text-PDF parsers for Meralco, CELCOR and Tarlac fall back to it when they stop finding their row.
- **Inbox** for sources that can't be fetched automatically (Facebook-only utilities): save the post's image as `data/inbox/<utility_id>/<name>.png` (optionally `<name>.url` with the post link) and run `verify`.

## The bill formula

`pipeline/billcalc.py`: bill(Q) = Q × Σ per-kWh charges + Σ per-customer charges + franchise tax + VAT − discounts; effective rate = bill ÷ Q, by convention at Q = 200 kWh. Tested against CELCOR's October 2026 schedule and typical bill (every non-tax line reproduced to the centavo). Used for schedules without totals (CEDC): the residential charges before tax are a floor DOE's all-in figure must clear.

## How IDs work

`utility_id` is a slug of the name DOE prints (`meralco`, `neeco-ii-area-i`, `leyeco-1-dorelco`). It's issued once: `doe.py` reads the committed `data/utilities.csv` first and keeps existing IDs, so a later rename in DOE's sheet doesn't change the key. A new name in DOE's sheet gets a new ID. Mapping that to an old one (a merger, a rename) is a manual edit to `utilities.csv`.

## Sources and caveats

- **Rates:** DOE, [electricity rates per distribution utility](https://docs.google.com/spreadsheets/d/1oGc1iGpacELS6tkHjesvAeKZo8v2iv4UkYFY04e0p-I), linked from [doe.gov.ph/data-and-prices/electricity](https://www.doe.gov.ph/data-and-prices/electricity). Values are DOE's, unchanged. Where the four tabs disagree on a utility's grid or region, the master list follows the Residential tab and the disagreement is logged.
- **PSGC:** PSA's 2026 PSGC as parsed by [bendlikeabamboo/barangay](https://github.com/bendlikeabamboo/barangay) `v2026.10.12.0` (MIT). Barangays are dropped at fetch.
- **Franchise areas:** the "Areas served" column of Wikipedia's [List of electric distribution utilities in the Philippines](https://en.wikipedia.org/wiki/List_of_electric_distribution_utilities_in_the_Philippines), which cites DOE's DU profiles and Distribution Development Plans. This is the weakest source in the set: it is a secondary source and should be replaced with ERC franchise records or NEA's coverage list. Coverage is only what that table lists. Nothing is inferred from a utility's name or region; a listed town the matcher can't place uniquely goes to `data/qa/unmatched_areas.csv` and is left out.
- **Boundaries:** PSA municipal/city areas, PSGC 3Q 2026, from [faeldon/philippines-json-maps](https://github.com/faeldon/philippines-json-maps) `v1.1.1` (MIT), medium resolution. BetterGov's `open-data-visualization` repo ships the same set at low resolution (no license on that repo).
- **Map:** MapLibre GL JS 6.16.0 (BSD-3, self-hosted in `site/vendor/`) over OpenFreeMap tiles (OpenStreetMap data, ODbL). The tiles are the site's only third-party request; a checkbox (`#…&base=0`) turns them off.
- **Medians** are unweighted: each utility counts once, regardless of customers. Months marked NDA and values outside 1–40 PHP/kWh are excluded from medians and charts but kept in the data.
- No personal data is collected or published. The only third-party request is the optional basemap tiles.

## License

Code: MIT. Compiled data: ODC-BY 1.0. The source data stays under its publishers' terms.
