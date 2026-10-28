# Vilnius 2027 — v21 work report

**Prepared:** 2026-10-28  
**Register snapshot:** 2026-10-28 (campaign-participant registration deadline)  
**Public app:** https://dziugasmarcenas-ui.github.io/vilnius-2027/  
**Public source:** https://github.com/dziugasmarcenas-ui/vilnius-2027  
**Local project:** `/Users/dziugasmarcenas/Downloads/manomiestas-v1.8`

> This report distinguishes what the software does from what the evidence supports. Passing tests prove software behaviour. They do not prove that a quotation is interpreted correctly, that a legal reading is right, that a human has reviewed the content, or that VRK has published a ballot. Those items are listed separately and remain blocked or pending where that is the truth.

## 1. Requirement status

| # | Requirement | Status | What is actually true |
|---|---|---|---|
| 1 | Official, current register data | **Done for the 2026-10-28 snapshot** | Register v2 snapshot from the VRK campaign-participant page: 57 national participant blocks, 4 Vilnius list entries, 2 mayor campaign participants. Candidate registration, final lists and ballot numbers are **not** published. |
| 2 | Registration stages kept distinct | **Done** | The UI and data separate campaign-participant registration, candidate-list registration (overlay only), and ballot publication. No page calls a campaign participant a confirmed election candidate. |
| 3 | Flexible ballot/list coverage | **Done** | The importer discovers arbitrary Vilnius organisations with stable `org_NN_slug` IDs, handles variable list counts, and accepts a published candidate-list overlay without code changes. Transition tests cover new organisations and variable counts. |
| 4 | Defensible evidence interpretation | **Done, with a known limit** | 32 quoted rows: 26 scoreable, 6 non-scoreable, 20 unknown. A row enters the score only when the quotation supports the **full** proposition, the quote is verified, and the row is scoreable. Missing evidence stays unknown and never becomes a negative score. All 26 scoreable positions are “agree”. |
| 5 | Lawful voting guide | **Done for the current code; official examples blocked** | Ballot guidance is tied to Election Code Articles 143, 158, 167, 168, 170 and 171. Preference votes: up to five, fewer for 2–5 candidate lists. The official 2027 layout is still “Informacija ruošiama”, and the page says so. |
| 6 | Resilient update process | **Done and tested offline** | Register refresh, publication-state transitions, candidate overlay, and snapshot-derived dates are tested. The live VRK refresh still needs a human to run and inspect it when data changes. |
| 7 | Clean public release | **Done** | Only the nine generated pages, public JSON, `.nojekyll`, `robots.txt` and the curated source are published. Raw VRK HTML fixtures, `.cache/`, and personal files are excluded. |
| 8 | Corrected public deployment verified | **Done** | The corrected build was pushed and the live pages, downloads, headers and privacy behaviour were checked against the deployed revision. Commit hash recorded in section 10 and in the review packet. |
| 9 | Accessibility and responsive QA | **Automated pass; manual review pending** | Lighthouse accessibility 1.0 on the audited pages; no horizontal overflow at 320 px and 390 px on all nine pages; the compare table reflows into per-statement cards at 620 px and below; result state checked at a 303 px viewport; no console errors. Screen-reader and keyboard review by a human is still pending. |
| 10 | Review workbench | **Done as a draft tool** | `review_2027.html` presents all 32 quoted rows with source link, source context, interpretation, coverage-gap reason and a decision form. Export is always `draft: true`, `human_approved: false`, `signoff: null`. The tool cannot sign off anything. |
| 11 | Evidence context and compare view | **Done** | Every one of the 32 quoted rows has a verified `source_context` snippet containing the quotation. `compare_2027.html` compares any two lists statement by statement, with quote, context, gap reason and a shareable `#a=…&b=…` fragment. |
| 12 | Coverage expansion | **Researched, no new scoreable position** | Liberalų sąjūdis pages found in October 2026 are 2022–2023 material or undated; TS-LKD’s participatory-budget passage is interleaved by two-column PDF extraction. Both were rejected as 2027 evidence. The scoreable count stays 26. |
| 13 | Voter practical guide | **Done** | `rinkejui_2027.html` covers eligibility, residence declaration, deadlines, advance/home/other-precinct voting, ID, assistance and accessibility, the mayoral runoff, and official citations. Dates come from the VRK calendar PDF and the Election Code. |
| 14 | Release hardening | **Done** | Source-watch, link audit, HTML structure audit, Lighthouse config and CI workflows were added. `--check` builds nine pages and verifies 20 public files. |

Two items cannot be completed by an agent and are not claimed: **independent human review** and **official 2027 ballot data**. They are blocked, not “passing”.

## 2. What was built

A static, nonpartisan guide for the **7 March 2027 Vilnius city council and mayoral elections**.

| Page | What it does |
|---|---|
| `index.html` | Overview, status warnings, entry points |
| `quiz_v21.html` | 6 topics / 13 statements; choose 1–3 topics, answer, set a priority, get a result and voting plan |
| `register_2027.html` | VRK campaign-participant register snapshot; separates “participant”, “candidate list”, and “final ballot” |
| `sources_2027.html` | Every source, every quotation, quote checks, coverage gaps, and non-scoreable reasons |
| `methodology_2027.html` | Scoring, coverage gate, close/no-match rules, limits, and why programme similarity is not “voting power” |
| `ballot_2027.html` | Ballot structure and marking guidance under the current Election Code; explicitly not an official ballot |
| `rinkejui_2027.html` | Practical voter guide: eligibility, deadlines, advance/home/other-precinct voting, ID, assistance, runoff, official sources |
| `compare_2027.html` | Two-list comparison by statement, with quotes, source context, gap reasons and a shareable link |
| `review_2027.html` | Human-review workbench for the 32 quoted rows; exports a draft that cannot sign off |

The app is static: no analytics, cookies, local storage, third-party scripts, or network calls. It uses a closed Content Security Policy (`default-src 'none'; style-src 'unsafe-inline'; img-src data:`).

## 3. Current official status

- Election: **2027-03-07**, Vilnius city district No. 57, 51 council seats.
- Campaign-participant registration ended **2026-10-28**.
- Candidate nomination ends **2026-12-17 17:00**.
- VRK candidate-registration decisions by **2027-02-04**.
- Final candidate lists, names, and ballot numbers are expected **2027-02-05**.
- As of the **2026-10-28** snapshot: **57 national participant blocks**, **4 Vilnius list entries** (TS-LKD, LS, LP, LSDP), **2 mayor campaign participants** (Vytautas Mitalas, LP; Simonas Gentvilas, LS).
- VRK's 2027 programme-publication and ballot-example pages still said “Informacija ruošiama”.

The app shows these as **pending**, not as positive confirmations.

## 4. Questionnaire and scoring

- 6 topics: transport, education, housing, governance/transparency, safety, economy.
- 13 statements.
- User selects 1–3 topics and a top priority.
- Coverage gate: **35%**.
- Minimum known answers: `min(3, answered_count)`.
- Minimum covered selected topics: `min(2, selected_topic_count)`.
- Top-topic weight: **2**.
- Statement points: `100 - 25 × |user_answer - party_stance|`.
- If the best score is below **50/100**, the app returns **no match** and does not recommend a list.
- `close_reason` distinguishes `margin`, `coverage`, and `margin_and_coverage`, so the UI does not claim “several lists are close” when only one list is eligible with thin coverage.
- A row counts only when `interpretation.supports_full_proposition === true`, the quote is verified, and the row is scoreable.

## 5. Evidence

`vaa/vilnius_2027_v21.json`

- 4 parties/lists, 6 topics, 13 statements.
- **52 organisation/statement combinations**.
- **32 quoted evidence rows**: **26 scoreable**, **6 non-scoreable**, **20 unknown**.
- **All 32 quoted rows carry a verified `source_context`** generated from the cached source text and checked by `python3 vaa_context_v21.py --check`.
- **26 combinations have no usable evidence**; all are documented in `vaa/review/coverage_gaps_v21.json` with a reason and, where one exists, the nearest partial passage.
- The 6 non-scoreable rows are retained with explicit reasons, not silently discarded.
- **All 26 scoreable positions are “agree.”** There is currently no scoreable “disagree” / “lean_disagree” row. The app discloses that the result mainly reflects **topic coverage**, not opposing camps.

Per-list scoreable rows: TS-LKD 7, LP 5, LS 5, LSDP 9.

The three rows that were downgraded from the earlier count (29 → 26) are retained as non-scoreable because the quotation supports only part of the proposition: TS-LKD A-lanes (one street, not the network), LP kindergarten places (a past result, not a 2027 promise), and LP civil shelters (a question, not a commitment).

## 6. Sources and quote verification

`vaa/sources_2027_v21.json` — **11 sources: 9 current 2027 documents/law + 2 Mano Balsas 2024 context sources.**

Current sources:

1. TS-LKD 2027 municipal programme PDF (published 2026-09-05; the PDF itself is still titled “programos projektas”).
2. LSDP Vilnius 2027 election programme PDF (2026-10-22).
3. LS 2027 programme/candidate presentation (2026-10-24).
4. LP transport programme page (2026-08-27).
5. LP kindergarten article (2026-10-09).
6. LP civil-defence article (2026-06-10).
7. LRT article on the Šiaurinė street A-lane statement (2026-10-07).
8. Election Code, current redaction in force 2026-09-02 – 2027-08-31.
9. VRK 2027 election calendar PDF (official dates and deadlines).

Context only: Mano Balsas methodology and 2024 national test.

`vaa/review/source_checks_v21.json` — **37 quotation checks**: 32 dataset quotations + 5 Mano Balsas methodology quotations. All checked quotations were found in the stored source text.

**2026-10-28 source re-check.** Two further 2027 sources were evaluated for coverage gains:

- `liberalai.lt` programme presentation — already in the dataset as `ls_programme_summary`; fully mined, no new scoreable positions.
- `tsajunga.lt` programme article — a report about the same “Savivalda kiekvienai dienai” document; the article says the congress approved it, but the published PDF still says “projektas”. Document status stays `draft`; no new quotations and no new scoreable positions.

Result: the scoreable count did not change.

## 7. Register and update process

`vaa/vilnius_2027_register.json` — v2 snapshot:

- 57 national participant blocks.
- 4 Vilnius list entries; 2 mayor campaign participants.
- Explicit flags: candidate lists **false**, candidate names **false**, ballot numbers **false**, programme publications **pending**, ballot examples **pending**.

`vaa/review/vrk_2027_manifest.json` — public register metadata and SHA-256 hashes. The raw scraped HTML is not published.

Update path, tested offline in `tests/test_vaa_transitions_v21.py`:

- a new Vilnius organisation is discovered and gets a stable `org_NN_slug` ID;
- list counts can change without code edits;
- a programme or ballot page can move from pending to published only when the page's own content region contains document links (global header/footer PDFs are ignored);
- a published candidate-list overlay adds names and numbers, and must cover every registered list;
- rejected lists must be marked explicitly;
- rendered snapshot dates follow the register, not hard-coded October dates.

`vaa/candidate_lists_2027.json` is the empty overlay and the only place future candidate names and ballot numbers may enter.

## 8. Voting guide and law

`vaa/law_2027.json` stores exact excerpts and source metadata for the Election Code, including Articles 8, 9, 16, 62, 84, 131, 132, 134, 135, 138, 139, 142, 143, 158, 167, 168, 170 and 171.

- **Articles 8–9:** who may vote; 60-day residence declaration; legal incapacity.
- **Article 16:** council and mayoral districts coincide with the municipality.
- **Article 62:** voting in another precinct of the same district.
- **Article 84:** publication of lists, candidates and ballot numbers.
- **Articles 131–135:** accessible premises, funding, advance/home voting, assistance.
- **Articles 138–139:** home-voting request and election-day hours.
- **Article 142:** accepted identity documents.
- **Article 143:** up to five preference votes; fewer for 2–5 candidate lists; all preference boxes have equal weight.
- **Article 158:** invalid preference marks.
- **Article 168(4):** 4% threshold for a list, 6% for a joint list.
- **Article 170:** quota-and-remainder seat allocation.
- **Article 171:** final order from preference votes.
- **Article 167(4):** separate mayoral election and runoff.

The ballot page says the council choice and the mayoral choice are **two separate decisions**, which VRK may present on separate ballot papers or different parts of one ballot. It defers the voting-day mechanics to the precinct commission. It does **not** claim the 2027 layout.

## 9. Privacy, accessibility and responsive QA

- **Privacy:** each page made exactly one network request — for itself. No cookies, no `localStorage`, no `sessionStorage`, no third-party requests.
- **Lighthouse:** accessibility **1.0** and best practices **1.0** on the audited pages. SEO is **0.8** only because the closed CSP blocks Lighthouse's own `robots.txt` fetch; `robots.txt` exists and is served correctly. This is a tooling artifact, not a site defect.
- **Responsive:** no horizontal overflow at **320 px** and **390 px** on all nine pages. The compare table becomes a stacked card layout at 620 px and below. The rendered result state had no overflow at a **303 px** viewport.
- **Console:** no console errors or page errors in the tested flows.
- **Manual review:** keyboard-only, 200% zoom, reduced-motion and screen-reader review by a human remain pending. Automated checks are not a substitute.

### Release hardening

- `vaa_watch_v21.py` re-fetches every manifest source and compares content and text hashes. Byte-only changes pass; a changed text hash fails and asks for a human re-read. `.github/workflows/source-watch.yml` runs weekly, uploads the report and opens or updates an issue.
- `vaa_links_v21.py` checks every local target, fragment, external HTTPS link and private-path leak; `--online` also requests external URLs.
- `vaa_audit_v21.py` checks one `h1`, heading order, duplicate ids, landmarks, labels, alt text, table headers, captions and inline handlers.
- `lighthouserc.json` and `.github/workflows/lighthouse.yml` run Lighthouse CI after the build, link audit and HTML audit.
- `tests/test_vaa_release_v21.py` covers watch classification, link resolution, HTML audit rules, importer fuzzing and the built-site audit.
- First live watch run (2026-10-28): 7 sources unchanged, 3 Liberalų sąjūdis pages byte-only changed with identical text, and **1 text change** — the LRT Šiaurinė article. The exact quoted sentence is still present in the new text, and the affected row is non-scoreable. No refresh was performed: refreshing would change the snapshot and needs a human re-read. The weekly workflow exists to prompt exactly that re-read.

### Test counts

- Local full suite: **449 tests, 0 failures, 0 errors**.
- Public source repo suite: **124 tests passed, 3 skipped**. The skips are fixture-dependent register-parser tests; the raw VRK HTML is intentionally not published.
- Build check: `python3 vaa_build_v21.py --check` → `OK: 9 pages, clean dist/, 20 checked files`.
- Link audit: **9 pages, 329 links, 0 errors, 0 warnings**.
- HTML audit: **9 pages, 0 errors, 3 warnings** (three secondary pages are not in the primary navigation, so they have no `aria-current`).

## 10. Public deployment

- GitHub Pages, source branch `main`, folder `/docs`.
- `docs/` is a byte copy of the clean `dist/`.
- Previous deployed revision: **`a5551848708ca054b8a0c7c53565114d17f8506c`** (GitHub Pages build status: `built`).
- This nine-page release: **`71d8b5f698ca259da86f23d2b3484185bfa966f8`** (GitHub Pages build status: `built`).
- Public data: https://dziugasmarcenas-ui.github.io/vilnius-2027/data/

Verification performed against the live site:

- all nine pages return HTTP 200 over HTTPS;
- the public JSON downloads return HTTP 200 and match the built files;
- `/fixtures/vrk_2027_dalyviai.html` and `/.cache/vaa_v21/` return 404;
- the live pages show the 2026-10-28 snapshot and the 26/6/20 evidence counts;
- no raw fixture, cache, or personal file is reachable;
- no third-party request is made.

## 11. What remains blocked or incomplete

These are real limitations, not test-count issues:

1. **No certified 2027 ballot yet.** Candidate names, final lists, ballot numbers, and official ballot examples do not exist publicly. Expected around 2027-02-05.
2. **No independent human validation.** Content, legal, accessibility, and real-user review remain pending. The app says so and publishes the pending review packet.
3. **All scoreable evidence is “agree.”** No scoreable opposing position currently exists in the dataset. The app discloses that the result is mainly a coverage/priority comparison.
4. **20 rows are unknown and 6 are non-scoreable.** Missing evidence is never converted into a negative score. Coverage gaps are published.
5. **TS-LKD participatory budget remains unknown.** The PDF text contains “plėsime dalyvaujamąjį biu-džetą”, but two-column extraction interleaves lines, so no exact quotation can be stored until a clean official text appears.
6. **TS-LKD document status is `draft`.** The party's own article says the programme was approved, but the published PDF still says “projektas”. The conservative status is kept.
7. **No professional legal signoff.** The app is a civic information tool, not legal advice.
8. **Coverage expansion found no new scoreable position.** Liberalų sąjūdis 2027 evidence could not be added from the pages found, and the TS-LKD participatory-budget passage cannot be quoted exactly until a clean text exists.
9. **The compare view is not a ranking.** It compares programme text on the same statements; it does not measure delivery, competence, or coalition power.

## 12. Where to look

- Main dataset: `vaa/vilnius_2027_v21.json`
- Official register: `vaa/vilnius_2027_register.json`
- Sources: `vaa/sources_2027_v21.json`
- Quote checks: `vaa/review/source_checks_v21.json`
- Coverage gaps: `vaa/review/coverage_gaps_v21.json`
- Human-review packet: `vaa/review/human_validation_v21.json`
- Build: `python3 vaa_build_v21.py --check`
- Context check: `python3 vaa_context_v21.py --check`
- Source check: `python3 vaa_sources_v21.py --check`
- Source watch: `python3 vaa_watch_v21.py`
- Link audit: `python3 vaa_links_v21.py`
- HTML audit: `python3 vaa_audit_v21.py`
- Tests: `python3 -m unittest discover -s tests -q`
- Public output: `dist/`
- Public source repo: https://github.com/dziugasmarcenas-ui/vilnius-2027
- Public site: https://dziugasmarcenas-ui.github.io/vilnius-2027/
