# Progress

Built 16 Nov 2026 (one evening) as the optional prototype for the BetterGov Fellowship application, due 17 Nov.

| Phase | Status | Notes |
|---|---|---|
| 1. Ingest DOE's rate sheet → tidy table, CSV/Parquet/datapackage | Done | 45,261 rows. Nov 2026 column is in the sheet but empty (not compiled yet). |
| 2. Master list v0 with PSGC | Done | 137/141 with province codes; the other 4 serve only independent cities (codes in `psgc_independent_city_codes`). 1,648/1,664 listed areas matched. |
| 3. Check DOE against utility sources | Done (2 utilities) | Meralco (residential + generation) and TEI (generation): 32/32 match. CELCOR, AEC, CEDC tried; not automatable without OCR or a working link. |
| 4. Static view + JSON API | Done, local | Verified in the browser at desktop and phone widths, light and dark. |
| 5. PROPOSAL-NOTES | Done | |
| 6. Map + methodology page | Done, local | Map tab (municipal choropleth, month slider, click to select a utility), all-utilities Table tab, generated methodology page. Patterned on the Naga budget portal's explorer research and methodology page. |
| 7. Source checks page, CELCOR, Data & API page | Done, local | CELCOR added through its archive's JSON API (43/54 match; all 11 differences are CELCOR residential). `checks.html`: every comparison with DOE's cell link and a docked viewer of the source page, value highlighted (Naga source-viewer pattern). `data.html`: downloads + Swagger-style reference from a generated OpenAPI 3.1 spec. Methodology checks section rewritten with sources and the tools assessment. |
| 8. Image reading, bill formula, basemap | Done, local | `extract.py` (OCR + vision model + schema + checks) reads AEC and CEDC images; text parsers fall back to it; inbox for Facebook-only utilities. `billcalc.py` formula, tested on CELCOR. Map moved to MapLibre over OpenFreeMap with click-to-zoom place panel. |
| 9. Outages exploration (branch `outages`) | Done, not merged | `pipeline/outages.py`: Meralco 2026 planned interruptions (HTML) and AEC since Sep 2026 (OCR + model), barangays → PSGC. `pipeline/reliability.py`: NEA SAIFI/SAIDI for 121 ECs, 10 quarters (reports on public Google Drive). ERC publishes none; NGCP has a today-only outlook feed. Meralco's terms forbid robots and republishing: decision needed before anything goes public. See `OUTAGES.md`. |
| Deploy | Done (17 Nov) | Public repo github.com/kimileeee/ph-energy-data; Vercel project ph-energy-data (scope kimileeees-projects), Git-connected; live at https://ph-energy-data.vercel.app. Link added to the Notion application page. |

## Deviations from the brief, and why

- **PSGC source.** Started with psgc.gitlab.io; its snapshot predates the Maguindanao split and the Negros Island Region, so province codes came out wrong for BARMM and NIR. Switched to PSA's 2026 PSGC via `bendlikeabamboo/barangay` v2026.10.12.0.
- **Coverage source.** No machine-readable ERC or NEA franchise list was reachable (nea.gov.ph returns 403). Used Wikipedia's DU list, which cites DOE's DU profiles. Flagged as the weakest source in README.
- **Third scraper.** The brief suggested Meralco, CELCOR, AEC. CELCOR's DOE link is a 404 page and its real archive renders client-side with no 2026 files. AEC posts rates as images, and its bill-insert PDF is a scanned image. Used Tarlac Electric (text PDFs) instead, and added a reachability audit of all 141 DOE source links.
- **TEI residential.** Not compared. TEI publishes unbundled charges, and DOE's residential number can't be rebuilt from them without DOE's method. Generation charge only.
- **DuckDB file** (as in the GAB dataset) skipped. Parquet reads directly in DuckDB.
- **Generation charge** shows in the view for the utility alone, without medians.
- **Map is a tab, not a separate page** (Kim asked for "some sort of map"): Chart / Map / Table over one URL state, as the Naga explorer research recommends after OWID Grapher. Colour by municipality using the utility's rate; shared places show the median of their utilities. Kalayaan not drawn.
- **CELCOR automated after all.** Its archive page is a JavaScript app, but it reads a public JSON API (`api.celcor.com.ph/Home/ratesarchives`), found by watching the page's network requests (what reverse-api-engineer automates). The PDFs are text.
- **Facebook not scraped.** agent-reach's Facebook channel only does search and page profiles, through a personal logged-in session copied from the user's Chrome, flagged experimental. Not installed: personal-account scraping breaks Facebook's terms and can't run unattended, and the posts are images anyway. Proposed instead: a monthly machine-readable filing.
- **Swagger-style UI hand-built**, not swagger-ui-dist (≈1.5 MB vendored): the site makes no third-party requests, and the spec itself (`openapi.json`) opens in any Swagger tool.
- **PSGC codes were numbers in JSON/Parquet** (leading zeros lost) until the OpenAPI lint caught it; fixed in `publish.py`.
- **Hand-built SVG map replaced by MapLibre** once Kim asked for a real basemap; the basemap tiles are the one exception to "no third-party requests" (switchable). Boundaries moved from low to medium resolution for street-level zoom. BetterGov's boundaries were checked: identical to faeldon low-res, unlicensed.
- **Facebook:** logged-out access tested (page HTTP 400; page widget is an empty JS shell). Not scraped; inbox + sanctioned routes documented instead.
- **Image extraction runs on the local `claude` CLI** because no API key is configured here; CI needs `ANTHROPIC_API_KEY` and Tesseract in place of Apple Vision.
- **Methodology figures are computed at build time** (`pipeline/site.py`), not fetched by JS, so the page reads with JS off.

## Next

1. Run the image reader in CI (Anthropic API key + Tesseract instead of Apple Vision) and extend discovery to more utilities; for the 54 Facebook-only utilities, the inbox now and a filing template later.
2. Ask DOE about the 18 residential differences (CELCOR's method, AEC's Aug 2026 jump, CEDC below its own pre-tax charges); see the checks page.
3. Replace the Wikipedia coverage source with ERC/NEA records; add ERC/NEA/DOE source IDs to the master list.
4. Monthly scheduled run (GitHub Actions) so the check report updates itself.
