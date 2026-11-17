# Power outages: what exists, and a first slice

Exploration for the energy prototype's outages strand, 17 Nov 2026. Branch `outages`; nothing here is on the live site.

## What exists

| Source | What it gives | Coverage | Format | Read here? |
|---|---|---|---|---|
| **Meralco** maintenance schedule + archive ([current](https://www.meralco.com.ph/residential/outages-center/scheduled-service-maintenance), [archive](https://www.meralco.com.ph/residential/outages-center/scheduled-service-maintenance/archive)) | Planned interruptions: date, time window, circuit, affected streets/subdivisions/barangays, reason | Meralco's franchise (NCR and parts of 6 provinces); **6,674 notices since May 2018** | Server-rendered HTML, consistent markup | **Yes**, 2026 (text parser) |
| **Angeles Electric (AEC)** advisories ([page](https://angeleselectric.com.ph/advisories/)) | Planned interruptions per barangay ("11-18-2026 Cutcut"), one notice each | Angeles City; **305 "System Advisories" posts** in the WordPress category | WordPress JSON API lists every post; each notice is a one-page PDF, **some text, some scanned images** | **Yes**, since Sep 2026 (OCR + vision model) |
| Meralco yellow/red alert locations ([page](https://www.meralco.com.ph/residential/outages-center/yellow-red-alert-locations)) | Areas and time blocks for rotational brownouts during grid alerts | Live only ("All Systems Normal" today); no history kept | HTML | No (nothing to read today); worth snapshotting during alerts |
| **NEA** "Compliance Report on the Performance of ECs" ([EC Classifications](https://nea.gov.ph/ec-classifications/)) | SAIFI, SAIDI, system/feeder loss and NEA's compliance flag per electric cooperative, quarterly (Appendix B) | 120–121 ECs, **2024 Q1 to 2026 Q2** (10 reports) | Text PDFs on public Google Drive folders, listed by the page | **Yes**, all 10 (text parser) |
| NEA, older reports | Same, 2018–2023 | — | PDF | **Gone**: the old `ao39` links return 404 since NEA's site redesign; the new page lists 2024 onward only |
| **ERC** SAIFI/SAIDI of private DUs | Reliability per private utility | 21 private DUs | — | **Not published** on erc.gov.ph: only the DU reportorial template (which asks utilities for reliability data) and performance-bonus scorecards. Needs an FOI request (foi.gov.ph/agencies/erc/) |
| **NGCP** Power Situation Outlook ([operations](https://www.ngcp.ph/operations#situation)) | Available capacity, peak demand and operating margin per grid, morning/afternoon/evening | Luzon, Visayas, Mindanao; **today only** | JSON behind the page | **No history**: no date parameter, scripts get Cloudflare's 403. A negative margin is red-alert territory (Visayas evening, 17 Nov: −448 MW). No alert archive on the site; press releases don't cover alerts |
| News coverage of NGCP alerts | Dates of alerts; hours when the article quotes NGCP | Patchy | HTML | Used only to locate dates, cited per row |
| **IEMOP** (market operator) monthly press releases | Total yellow/red alert hours per grid per billing month, sometimes | When IEMOP chooses to say it | HTML prose | One figure (Visayas, Aug 2026 billing period) |
| CELCOR | Service advisories | Cebu City area | Its site is a JavaScript shell; the JSON API behind it has a rates endpoint, no advisories endpoint found | No |
| Facebook-only utilities | Outage posts, usually as images | **54 of 141 utilities** (47 ECs, 7 private): DOE's own source link for them is a Facebook page | Images in posts | **No** (Meta's terms: no logged-in or headless scraping). The existing inbox (`data/inbox/<utility_id>/`) takes a saved image and reads it with the same OCR + model path |

The 54 are: decorp, ieec, oedc, sfelapco, ceneco-nepc, cepalco, ilpi, abreco, ifelco, kaelco, panelco-i, batanelco, iselco-ii, quirelco, neeco-ii-area-ii, pelco-ii, pelco-iii, tarelco-i, zameco-i, zameco-ii, quezelco-ii, biselco, ormeco, romelco, ficelco, maselco, soreco1, soreco2, tiselco, noceco, noneco, noreco-i, anteco, capelco, boheco-i, cebeco-ii, celco, bileco, esamelco, leyeco-1-dorelco, leyeco-iv, norsamelco, samelco-ii, zamsureco-ii, buseco, camelco, moresco-i, moresco-ii, daneco-nordeco, dasureco, socoteco-i, sukelco, surneco, baselco. "DOE links Facebook for rates" is a proxy for "posts outages only on Facebook"; it was not checked utility by utility.

## Licensing and terms

- **Meralco: decision needed before anything is published.** Its [Terms and Conditions](https://www.meralco.com.ph/terms-conditions) say site content "cannot be used without prior written consent", forbid users to "publish, sell or commercialize any of our Content in any media", and forbid navigating the site with "spiders, robots … or intelligent agents" other than generally available browsers. Downloadable documents may be used except for unrelated commercial gain. Dates, times and place names are facts rather than expression, but the robots clause covers how they were collected, and `areas_as_printed` copies Meralco's prose verbatim. The existing rate checks (`verify.py`) also fetch Meralco's site by script, so this is one decision for both: ask Meralco (or have DOE/ERC require the filing) before the outages table goes public. No robots.txt is served.
- **AEC:** public WordPress REST API; robots.txt allows everything except `/wp-admin/`; no terms of use on the site (privacy policy only). Notices are public safety announcements.
- **NEA, ERC, NGCP:** government and grid-operator publications behind Cloudflare challenges. Kim passed the checks by hand in Chrome to look around; nothing was bypassed. NEA's reports themselves sit on public Google Drive, which a script reaches without a check. NGCP's feed and ERC's site stay browser-only.
- **News:** cited per row as the place a date was found, not as the source of the alert. No article text copied beyond the times they quote from NGCP.
- **PSGC barangays:** PSA's 2026 PSGC via `bendlikeabamboo/barangay` v2026.10.12.0 (MIT), the same pinned release as the existing places file.

## The slice

| File | Rows | What it is |
|---|---|---|
| `data/outages/interruptions.csv` | 114 | One row per interruption window: `interruption_id`, `utility_id`, `start`/`end` (ISO, +08:00), `hours`, `kind` (scheduled, emergency…), `reason`, `circuit`, the window and areas as printed, `source_url`, `document_url` + `document_sha256` (AEC), `read_by`, `status` (ok / review) and `checks` |
| `data/outages/interruption_areas.csv` | 660 | One row per (window, barangay): barangay as printed, 10-digit `psgc_code` when exactly one candidate fits, its city, `match` (matched / ambiguous / not found), `match_scope`, `evidence` (the text it came from; for AEC the OCR line and its box on the page) |
| `data/qa/outage_places_outside_coverage.csv` | 1 | Places a notice names that the franchise list (Wikipedia-based coverage) doesn't have |
| `data/outages/grid_alerts_located.csv` | 34 | Grid alerts located in news coverage, 2026: date, grid, level, hours only where the article quotes NGCP's advisory, the article as `source_url`. A sample, not a complete list |
| `data/outages/grid_alert_hours_iemop.csv` | 1 | IEMOP's stated alert hours per grid per billing period (Visayas, 26 Jul–25 Aug 2026: 86 h yellow, 53 h red) |
| `data/outages/reliability_nea.csv` | 1,208 | NEA Appendix B, one row per (utility, quarter): `utility_id`, `saifi`, `saidi_minutes`, `loss_pct`, NEA's flag for each (C/NC), `missing` (NEA's code where it printed none), `basis`, the Drive file, page and row as printed, file SHA-256 |
| `data/qa/nea_reliability_issues.csv` | 25 | Year-to-date figures that fall from one quarter to the next within a year |
| `data/outages/reliability_sources.csv` | 13 | NEA's 10 report files, the gone 2018–2023 links, ERC and NGCP: what each holds and its status |
| `data/raw/psgc/barangays.csv` | 42,010 | Barangays from the same PSGC release (the existing `places.csv` drops them at fetch) |

### What the slice holds (17 Nov 2026)

| | Meralco | AEC |
|---|---|---|
| Notices read | 60 (2026: 54 archive + 6 current) | 42 of 46 posted since 1 Sep 2026 |
| Interruption windows | 72, 327 hours in all | 42, 200 hours |
| Read by | HTML parser | OCR + vision model + checks |
| Checks | 72 ok | 41 ok, 1 to review (a barangay the OCR didn't find) |
| Barangay rows | 611 | 49 |
| Matched to a PSGC code | **513 (84%)** | **46 (94%)** |
| Not matched | 95 not found, 3 ambiguous | 3 not found |

- **Meralco's archive stops at 24 Aug 2026** for this year, and the current page holds only the next few days (18–21 Nov). Notices from late August to mid-November aren't online today; a scheduled run that keeps each week's page is the only way to keep them.
- **AEC: 4 notices weren't read** because the local `claude` CLI hit its usage limit mid-run. They're listed in `data/qa/outage_notices_unread.csv`; a re-run reads them (cached reads are kept).
- **Unmatched barangays are left unmatched on purpose.** The common reasons: the notice spells it differently from PSGC ("Ibayong Silangan" vs "Ibayo Silangan", "Trece Martirez"), the place isn't a barangay ("Project 8", "Town Proper", "City Proper"), or a series the notice itself garbles ("Anabu I-A I-C"). Each row keeps the text it came from.
- **The notices check the coverage source.** Meralco posts interruptions in **Baliwag City**, which the Wikipedia-based franchise list doesn't include (`data/qa/outage_places_outside_coverage.csv`); its barangays are therefore not matched. AEC's notices name **Telabastagan** (a barangay of the City of San Fernando) and **Calibutbut** (a barangay of Bacolor, though one notice prints it as a sitio), outside its franchise list (Angeles City and part of Mabalacat); they stay unmatched until someone confirms where AEC's lines run. Notices are a better coverage source than Wikipedia, and an easy one to add to the master list later.
- **Reliability (NEA):** all 121 cooperatives join to `utility_id` (one crosswalk: NEA's CENECO is DOE's CENECO/NEPC). The quarterly figures rise through each year and restart each Q1, so they read as year to date; NEA doesn't say so, and the CSV marks the basis as inferred. Full-year 2025 (Q4): median SAIFI 11.9 interruptions and SAIDI 649 minutes per customer, against NEA's on-grid standards of 25 and 2,700. The outliers are in BARMM and nearby: BASELCO 16,528 minutes (275 hours) and 384 interruptions per customer, CASELCO 9,393, LASURECO 7,754, SIASELCO 5,456. 50 of 1,208 rows have no figure because the cooperative sent none (NEA prints dns, nda or nds), and NEA flags those as non-compliant, so compliance counts mix "unreliable" with "didn't report".
- **NEA's own figures need questions.** 25 year-to-date values fall within a year (`data/qa/nea_reliability_issues.csv`), some sharply: MOELCI I's 2024 SAIDI goes from 1,163 minutes in Q3 to 47 in Q4, PALECO's from 909 to 93, TIELCO's from 791 to 89. Either the basis isn't year to date for every cooperative, or the Q4 figures are a different period or a typo. A question for NEA, like the DOE rate differences.
- **Grid alerts:** news coverage located 34 alert rows across the three grids from April to September 2026; hours only for 18 of them, where the article quotes NGCP's advisory, and two days where articles disagree (noted per row). News counts the Visayas' red alerts in the dozens by late August ("87th red alert", Tribune, 26 Aug), so the table is a sample. IEMOP's one stated figure: 86 hours yellow and 53 hours red in the Visayas in the 26 Jul–25 Aug billing period.

### How it reads and matches

- **Meralco** (`pipeline/outages.py`, `meralco()`): the current schedule cards and this year's archive notices. One window per "BETWEEN … AND …" line; the date comes from the line when Meralco prints it (windows past midnight) and from the notice otherwise. Raises when the markup or a window line changes, never returns nothing.
- **AEC** (`aec()`): every post in the "System Advisories" category since 1 Sep 2026, via the WordPress API. The notice (PDF or image) goes through the existing layout-free reader in `pipeline/extract.py` (Apple Vision OCR, then the vision model into a new `OUTAGE_SCHEMA`: notice type, windows, barangays as printed, reason). `read_with_model` now takes a prompt and schema, so rates and outages share one reader. Deterministic checks then decide `status`: every barangay must be in the OCR text, the printed date must be in the OCR text, a window must end after it starts, and the date at the start of AEC's post title ("11-18-2026 Cutcut") must be one of the window dates. Reads are cached by file hash plus a hash of prompt, schema and model.
- **Barangays to PSGC** (`Places`, `barangay_mentions`): only inside the utility's franchise cities from `data/utility_areas.csv`. A city printed with the barangay ("Bgy. B. F. Homes, Parañaque City", "Bgys. A and B in Imus City") is binding; without one, the search is limited to the cities the notice's title and location name. Exactly one candidate or nothing: "San Isidro" in two named cities stays `ambiguous`. A city outside the franchise list binds too, so its barangays can't match somewhere else by name. Names compare after expanding Sto./Sta./Gen., Roman numerals (Amaya 1 = Amaya I) and dropping Bgy./City. A town name in the middle of a barangay list is read as a barangay (Dasmariñas has a Bgy. San Mateo), since many barangays share town names.

## Recommendation: an Outages view, later and narrow

Yes, starting with NEA's reliability indices (point 3), which can go on the site now. Planned interruptions come later and narrow, not as a "brownout map of the Philippines": they exist for the few utilities that publish them on a website, so a national picture would be mostly blank, and the blank would look like reliability.

1. **Settle Meralco first.** Ask Meralco for consent (or a feed), or ask DOE/ERC to make planned-interruption notices a filing like the rate filing the proposal already argues for: utility, barangay PSGC codes, start, end, reason, as CSV. The proposal's monthly machine-readable filing can carry both. Until then, show AEC only, or keep outages off the site.
2. **When it ships, put it on the utility, not a new map.** A section on the explorer's utility view: "Planned interruptions, last 90 days" (count, total hours, the barangays affected most, each linking to the notice). It reuses the URL-hash state (`du=`), needs no new state, and the checks-page viewer pattern can show the notice image with the barangay boxed (AEC rows already carry OCR boxes). A barangay-level map can come later, from `interruption_areas.csv`'s PSGC codes, once barangay boundaries are vendored.
3. **Reliability is the strongest first Outages view, and it's ready.** NEA's table covers all 120 cooperatives for 10 quarters, joins on `utility_id`, has no terms problem, and answers the comparison people want ("how often does my power go out, compared with elsewhere"). Show the full-year SAIFI and SAIDI on each cooperative's page beside its rate, and a map coloured by SAIDI on the existing municipal choropleth (through `utility_areas.csv`), with no-data hatched as the rate map does. Say "year to date (inferred)" and link the 25 issues. The 21 private utilities stay blank until ERC answers an FOI request; label that blank as "ERC doesn't publish", not as no outages.
4. **Grid alerts** need NGCP's advisories or the outlook feed as data (ask NGCP or DOE for the history). Until then, the news-located table is a sample for the proposal, not a dataset. Snapshotting Meralco's yellow/red alert locations page during alerts would add the rotational-brownout areas, subject to point 1.

Not done here: site pages for either, `reliability_nea.csv` in the pipeline's `__main__` run order, `publish.py`/datapackage/OpenAPI entries for the new files, AEC notices before September 2026 (a re-run with `AEC_SINCE` moved back; about 25 s of model time per notice), Meralco before 2026 (free to parse, but see point 1).
