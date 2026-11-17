# Proposal notes: numbers for the Energy answers

Numbers as of 16 Nov 2026, from `data/stats.json` and `reports/discrepancy-report.md` (and `site/methodology.html`, which states them from the build). Rerun the pipeline and these move.

## Coverage

- **141 distribution utilities** in DOE's sheet: 21 private (PIOU), 120 electric cooperatives. (The application draft says 142; DOE's sheet has 141. Wikipedia's list has 152 because it includes LGU-run and economic-zone utilities DOE's sheet leaves out.)
- **106 months compiled**, Jan 2018 to Oct 2026. Nov 2026 is a column in the sheet but still empty.
- **44,838 rate cells** (141 utilities × 106 months × 3 customer classes); **27,534 have a value**.
- **38.6% of rate cells are "NDA"** (no data available): residential 28.0%, commercial 41.2%, industrial 46.6%. Generation charge: 44.9% NDA.
- NDA is falling over time but not gone: about 60% in 2018–2019, 10% in 2023, 24% in 2026 so far. Off-grid utilities are worse (42% residential NDA vs. 26% on-grid).
- In the latest month (Oct 2026), **113 of 141 utilities (80%)** have a residential rate. Range ₱5.33 to ₱19.36/kWh; national median ₱12.72.
- **Master list v0:** every utility has a stable ID; **137 of 141 have province PSGC codes** for their franchise area; the other 4 serve only independent cities, which have their own codes. **1,648 of 1,664 listed cities and municipalities matched to PSGC**; 16 are logged for a human.

## Checks ("make the manual count automatic")

- **DOE vs. the utility's own documents:** five utilities. Meralco, CELCOR (via the JSON API behind its archive page) and Tarlac Electric from text PDFs; Angeles Electric and Clark Electric from images (OCR + vision model + arithmetic checks). Last 12 months, plus every older month DOE has no figure for where the utility's archive reaches (Meralco and CEDC, back to 2018). **All 70 generation charges match DOE. 43 residential figures differ**: Meralco 24, CELCOR 11, CEDC 6, AEC 2. Every figure is on the checks page with the source page open and the value highlighted, next to a link to DOE's exact cell.
- **Gaps filled: 52 cells DOE's sheet leaves empty** now have a value from the utility's own document (`data/filled.csv`): CEDC's generation charge for 26 months (Jan 2018 to Dec 2019 from undated screenshots, Jul 2025, Oct 2026), Meralco's generation charge for 25 months (Jan 2018 to Dec 2019, Nov 2026), Meralco's residential rate for Nov 2026.
- **Meralco 2018–2019:** DOE's residential figure equals Meralco's own **400 kWh** bill in 21 of 24 months; since Dec 2025 it is the 200 kWh bill to the centavo. The level DOE reads changed, and it isn't published.
- **Angeles Electric:** its notices give month-on-month changes. In Aug 2026 DOE's figure jumps +₱2.89 while AEC's notice says −₱0.0019 (two notices print it; the components add up). Following AEC's own changes, DOE's AEC series sits ₱2.71–2.89 too high through Oct 2026.
- **Clark Electric:** it publishes only a schedule; by the bill formula, its residential charges before VAT and taxes exceed DOE's all-in figure in 6 months, which an all-in rate can't do.
- **CELCOR (11 differences):** DOE's figure is below CELCOR's own 200 kWh bill in 9 of 11 months (by up to ₱1.37/kWh, Jul 2026) and above it in 2; no consumption level in CELCOR's table gives DOE's number.
- **Speed:** Meralco had already published November 2026 rates; DOE's November column was still empty. An automated pull has them the day they're posted.
- **Problems in DOE's sheet itself: 23 found automatically.**
  - 15 values outside 1–40 PHP/kWh: negative rates (PRESCO −0.73 generation, Mar 2025), a ₱6,447 commercial rate (BANELCO, Dec 2018, likely a misplaced decimal), BISELCO at ₱0.83 for three months.
  - 7 rows where the four tabs disagree on the same utility's grid, region or name (ILECO II/III on-grid vs. off-grid, CLPC's region).
  - 1 cell with a date typed in place of a rate (NONECO, Oct 2026: "Jan-16").
- **DOE's source links:** 78 point to a website, **54 to a Facebook page** (not machine-readable without login), 9 have no link. Of the 78 websites, 20 did not return HTTP 200 on 16 Nov (403, 404, timeouts). CELCOR's link shows the site's own "Error 404" page.
- **Machine-readability, sampled:** Meralco, CELCOR and TEI publish text PDFs (automatable today; CELCOR's are behind a JSON API its website uses). AEC and CEDC post rate tables as images (need OCR). Facebook-only utilities (54) can't be read without a logged-in account; the fix proposed is a monthly machine-readable filing to DOE/ERC, not scraping Facebook.

## Facebook-only utilities and image sources (for "How will you deliver" / risks)

- 54 of 141 utilities are linked by DOE only to a Facebook page. Logged out, Facebook gives a script nothing; logged-in or headless scraping is against Meta's automated-collection terms. Plan, in order: (1) **now**, an inbox where DOE staff drop the post image they already open each month, and the reader checks it; (2) utilities connect their own Page to a DOE/NEA app (Graph API, one-time per utility, NEA can ask the 120 cooperatives); (3) Meta's Page Public Content Access or Content Library for research/nonprofit partners; (4) the lasting fix, a monthly rate filing to DOE in any format.
- Images and scans are read by **OCR + a vision model into one fixed schema, then checked deterministically** (printed value found in the OCR, components add up, the same figure in two documents agrees). No per-layout parsers, so a redesigned notice doesn't break the pipeline; a failed check sends the document to review.
- **The formula:** effective rate = (Q × per-kWh charges + per-customer charges + franchise tax + VAT − discounts) ÷ Q, conventionally at Q = 200 kWh. Verified against CELCOR's own schedule and bill to the centavo.

## One-liners for the form

- Prototype sentence (Page 5, after "Your solution"; in Notion): *A working prototype already turns DOE's monthly rate sheet into an open dataset and API for all 141 utilities (Jan 2018–Oct 2026), with PSGC codes for 137 franchise areas and automated checks of DOE's figures against five utilities' own rate documents, PDFs and images: https://ph-energy-data.vercel.app (code: https://github.com/kimileeee/ph-energy-data).*
- Prototype link for the form's optional field: https://ph-energy-data.vercel.app
- Why it matters: *38.6% of DOE's rate cells are empty, 54 of 141 utilities publish rates only on Facebook, and a utility's November rates sit on its website while DOE's column is still blank.*
- Credibility: *Reading five utilities' own rate documents (PDFs and images), every generation charge DOE entered matched (70 of 70), and the same documents filled 52 cells DOE's sheet leaves empty. Where residential figures differ, the documents show why: in 2018–2019 DOE read Meralco's 400 kWh bill, today its 200 kWh bill. Automation makes the count faster and shows exactly where to look.*
