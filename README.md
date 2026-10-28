# Vilnius 2027 — ManoMiestas

A small, static, nonpartisan tool for the **7 March 2027 Vilnius city council and mayoral elections**. It compares a voter's answers with quoted, dated positions from the four Vilnius list entries in the VRK campaign-participant register as of **28 October 2026**.

**Public site:** https://dziugasmarcenas-ui.github.io/vilnius-2027/

## What is and is not published

- **Election:** 2027-03-07, Vilnius city district No. 57, 51 council seats.
- **Register snapshot:** 2026-10-28 (the campaign-participant registration deadline). The register had **57 national participant blocks**, **4 Vilnius list campaign-participant entries** (TS-LKD, LS, LP, LSDP) and **2 mayor campaign participants**.
- **Campaign participants are not election candidates.** The two named people — Vytautas Mitalas (LP) and Simonas Gentvilas (LS) — are registered **campaign participants for mayor**. Their registration as election candidates is not confirmed by this snapshot.
- **Not published:** candidate registration decisions, final candidate lists, candidate names, ballot numbers, and official 2027 ballot examples. VRK's programme and ballot pages say “Informacija ruošiama”; final lists are expected by 2027-02-05.
- **Evidence:** 52 organisation/statement combinations; **32 quoted rows**, of which **26 are scoreable**, **6 are retained as non-scoreable with a stated reason**, and **20 are unknown**. All 26 scoreable positions are “agree”, so the result mainly reflects which topics a list covers, not opposing camps. This is disclosed in the app.
- **Coverage gaps:** every missing or partial position is documented in `data/coverage_gaps_v21.json`, including the nearest partial passage where one exists.
- **Human review:** still pending. The app displays that limitation and publishes the review packet at `data/human_validation_v21.json`. Automated technical checks are not a substitute for independent content, legal, accessibility, or user review.

## Pages

| Page | Purpose |
|---|---|
| `index.html` | Overview and entry points |
| `quiz_v21.html` | 6 topics / 13 statements; topic selection, priority, results, coverage, ballot plan |
| `register_2027.html` | Official VRK campaign-participant register snapshot and pending-publication flags |
| `sources_2027.html` | All sources, every quotation, quote checks, coverage gaps, and non-scoreable reasons |
| `methodology_2027.html` | Scoring, coverage gates, close/no-match rules, limits, and why programme similarity is not “voting power” |
| `ballot_2027.html` | Ballot structure and marking guidance under the current Election Code; explicitly not an official ballot |

The public site is entirely static. It has no analytics, cookies, local storage, third-party scripts, or network calls. Pages use a closed Content Security Policy (`default-src 'none'`; inline styles and the quiz's inline script are the only allowances). The only network request a page makes is for itself.

## Rebuild and test

```bash
python3 vaa_build_v21.py --check
python3 -m unittest discover -s tests -q
./build_docs.sh
```

`build_docs.sh` rebuilds the six pages, verifies the clean public output, and replaces `docs/` with `dist/`.

The source scripts check:
- dataset schema, evidence gates, source URLs, quote hashes;
- Python/JavaScript scoring parity;
- official register semantics, publication states, and anti-fabrication flags;
- transition tests for a new organisation, variable list counts, published programme/ballot documents, and a final candidate-list overlay;
- Mano Balsas 2024 context-only boundary;
- generated pages, local links, CSP, and disclosure text.

## Data and sources

Curated data lives in `vaa/`; public downloads are copied to `docs/data/`. The source registry distinguishes 2027 campaign documents and the Election Code from Mano Balsas 2024 context. No raw scraped HTML, research cache, or personal file is published.

Important limitations are shown in the app:
- A missing position is **not** treated as disagreement.
- A party with thin coverage is not recommended as a single winner.
- If the best score is below 50/100, the app returns **no match** and does not recommend a list.
- Preference-vote guidance follows Election Code Article 143 (up to five preference votes, fewer for 2–5 candidate lists); the 2027 ballot layout is still unpublished.
- Programme-text similarity is not a prediction of seats, coalition influence, or future delivery.

## License

MIT. See `LICENSE`.
