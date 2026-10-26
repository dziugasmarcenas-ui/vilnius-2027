# Vilnius 2027 — ManoMiestas

A small, static, nonpartisan tool for the **7 March 2027 Vilnius city council and mayoral elections**. It compares a voter's answers with quoted, dated positions from the four lists registered in the Vilnius city district (No. 57) as of 26 October 2026.

**Public site:** GitHub Pages `/docs` (URL added after deployment).

## What is and is not published

- **Election:** 2027-03-07, Vilnius city district No. 57, 51 council seats.
- **Registered lists:** TS-LKD, LS, LP, LSDP.
- **Registered mayor candidates:** Vytautas Mitalas (LP), Simonas Gentvilas (LS). TS-LKD and LSDP had no registered mayoral candidate in the 2026-10-26 VRK register.
- **Not published:** final candidate lists, candidate names, ballot numbers, and official 2027 ballot examples. VRK's pages say “Informacija ruošiama”; final lists are expected by 2027-02-05.
- **Evidence:** 32 quoted rows; 29 scoreable and 3 retained as non-scoreable with a stated reason. All 29 scoreable 2026-10-26 positions are “agree”, so the result mainly reflects which topics a list covers, not opposing camps. This is disclosed in the app.
- **Human review:** still pending. The app displays that limitation and publishes the review packet at `data/human_validation_v21.json`. Automated technical checks are not a substitute for independent content, legal, accessibility, or user review.

## Pages

| Page | Purpose |
|---|---|
| `index.html` | Overview and entry points |
| `quiz_v21.html` | 6 topics / 13 statements; topic selection, priority, results, coverage, ballot plan |
| `register_2027.html` | Official VRK participant-register snapshot and pending-publication flags |
| `sources_2027.html` | All sources, every quotation, quote checks, and non-scoreable reasons |
| `methodology_2027.html` | Scoring, coverage gates, close/no-match rules, limits |
| `ballot_2027.html` | Ballot structure and preliminary guidance; explicitly not an official ballot |

The public site is entirely static. It has no analytics, cookies, local storage, third-party scripts, or network calls. Pages use a closed Content Security Policy (`default-src 'none'`; inline styles and the quiz's inline script are the only allowances).

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
- official register semantics and anti-fabrication flags;
- Mano Balsas 2024 context-only boundary;
- generated pages, local links, CSP, and disclosure text.

## Data and sources

Curated data lives in `vaa/`; public downloads are copied to `docs/data/`. The source registry distinguishes 2027 campaign documents from Mano Balsas 2024 context. No raw scraped HTML, research cache, or personal file is published.

Important limitations are shown in the app:
- A missing position is **not** treated as disagreement.
- A party with thin coverage is not recommended as a single winner.
- If the best score is below 50/100, the app returns **no match** and does not recommend a list.
- Preference-vote guidance is labelled as preliminary 2023 practice until VRK publishes the 2027 rules.

## License

MIT. See `LICENSE`.
