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
| 7 | Clean public release | **Done** | Only six pages, public JSON, `.nojekyll`, `robots.txt` and the curated source are published. Raw VRK HTML fixtures, `.cache/`, and personal files are excluded. |
| 8 | Corrected public deployment verified | **Done** | The corrected build was pushed and the live pages, downloads, headers and privacy behaviour were checked against the deployed revision. Commit hash recorded in section 10 and in the review packet. |
| 9 | Accessibility and responsive QA | **Automated pass; manual review pending** | Lighthouse accessibility 1.0 on the audited pages; no horizontal overflow at 320 px and 390 px; result state checked at a 303 px viewport; no console errors. Screen-reader and keyboard review by a human is still pending. |

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
- **26 combinations have no usable evidence**; all are documented in `vaa/review/coverage_gaps_v21.json` with a reason and, where one exists, the nearest partial passage.
- The 6 non-scoreable rows are retained with explicit reasons, not silently discarded.
- **All 26 scoreable positions are “agree.”** There is currently no scoreable “disagree” / “lean_disagree” row. The app discloses that the result mainly reflects **topic coverage**, not opposing camps.

Per-list scoreable rows: TS-LKD 7, LP 5, LS 5, LSDP 9.

The three rows that were downgraded from the earlier count (29 → 26) are retained as non-scoreable because the quotation supports only part of the proposition: TS-LKD A-lanes (one street, not the network), LP kindergarten places (a past result, not a 2027 promise), and LP civil shelters (a question, not a commitment).

## 6. Sources and quote verification

`vaa/sources_2027_v21.json` — **10 sources: 8 current 2027 documents/law + 2 Mano Balsas 2024 context sources.**

Current sources:

1. TS-LKD 2027 municipal programme PDF (published 2026-09-05; the PDF itself is still titled “programos projektas”).
2. LSDP Vilnius 2027 election programme PDF (2026-10-22).
3. LS 2027 programme/candidate presentation (2026-10-24).
4. LP transport programme page (2026-08-27).
5. LP kindergarten article (2026-10-09).
6. LP civil-defence article (2026-06-10).
7. LRT article on the Šiaurinė street A-lane statement (2026-10-07).
8. Election Code, current redaction in force 2026-09-02 – 2027-08-31.

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

`vaa/law_2027.json` stores exact excerpts and source metadata for the Election Code.

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
- **Responsive:** no horizontal overflow at **320 px** and **390 px** on all six pages. The rendered result state had no overflow at a **303 px** viewport.
- **Console:** no console errors or page errors in the tested flows.
- **Manual review:** keyboard-only, 200% zoom, reduced-motion and screen-reader review by a human remain pending. Automated checks are not a substitute.

### Test counts

- Local full suite: **424 tests, 0 failures, 0 errors**.
- Public source repo suite: **99 tests passed, 3 skipped**. The 3 skips are fixture-dependent register-parser tests; the raw VRK HTML fixture is intentionally not published.
- Build check: `python3 vaa_build_v21.py --check` → `OK: 6 pages, clean dist/, 17 checked files`.

## 10. Public deployment

- GitHub Pages, source branch `main`, folder `/docs`.
- `docs/` is a byte copy of the clean `dist/`.
- Deployed revision: **recorded below after the push and live verification.**
- Public data: https://dziugasmarcenas-ui.github.io/vilnius-2027/data/

Verification performed against the live site:

- all six pages return HTTP 200 over HTTPS;
- the public JSON downloads return HTTP 200 and match the built files;
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

## 12. Where to look

- Main dataset: `vaa/vilnius_2027_v21.json`
- Official register: `vaa/vilnius_2027_register.json`
- Sources: `vaa/sources_2027_v21.json`
- Quote checks: `vaa/review/source_checks_v21.json`
- Coverage gaps: `vaa/review/coverage_gaps_v21.json`
- Human-review packet: `vaa/review/human_validation_v21.json`
- Build: `python3 vaa_build_v21.py --check`
- Tests: `python3 -m unittest discover -s tests -q`
- Public output: `dist/`
- Public source repo: https://github.com/dziugasmarcenas-ui/vilnius-2027
- Public site: https://dziugasmarcenas-ui.github.io/vilnius-2027/
