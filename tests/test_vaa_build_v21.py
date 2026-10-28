"""Build and public-page regression tests for the v21 static output."""
import json
import re
import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from vaa_build_v21 import DIST, PAGES, build, check, sync_party_mayors  # noqa: E402


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.ids = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            self.ids.append(values["id"])
        if tag == "a" and "href" in values:
            self.links.append(values["href"])


class BuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pages = build()
        check()

    def test_all_pages_and_curated_data_are_built(self):
        for name in PAGES:
            self.assertTrue((ROOT / name).is_file(), name)
            self.assertTrue((DIST / name).is_file(), name)
            self.assertGreater((DIST / name).stat().st_size, 1000, name)
        for name in (
            "vilnius_2027_v21.json",
            "vilnius_2027_register.json",
            "sources_2027_v21.json",
            "source_manifest_v21.json",
            "source_checks_v21.json",
            "manobalsas_2024_context.json",
            "human_validation_v21.json",
            "vrk_2027_manifest.json",
        ):
            self.assertTrue((DIST / "data" / name).is_file(), name)
        self.assertTrue((DIST / ".nojekyll").is_file())

    def test_dist_is_clean_and_contains_only_public_files(self):
        allowed = set(PAGES) | {".nojekyll"}
        files = {
            path.relative_to(DIST).as_posix()
            for path in DIST.rglob("*")
            if path.is_file()
        }
        self.assertTrue(allowed.issubset(files))
        for path in files:
            self.assertFalse(path.startswith(".cache"), path)
            self.assertFalse(path.startswith("fixtures"), path)
            self.assertFalse(path.startswith("docs"), path)
            self.assertFalse(path.startswith(".claude"), path)
            self.assertNotIn("palace.yaml", path)
            self.assertNotIn("research", path)

    def test_every_page_discloses_the_unpublished_ballot(self):
        for name in PAGES:
            text = (DIST / name).read_text("utf-8")
            if name == "ballot_2027.html":
                self.assertIn("Oficialūs biuletenių pavyzdžiai dar nepaskelbti", text, name)
            else:
                self.assertRegex(text, r"Oficialus( 2027 m\.)? biuletenis dar nepaskelbtas", name)

    def test_index_title_is_not_duplicated(self):
        text = (DIST / "index.html").read_text("utf-8")
        self.assertIn("<title>Rinkimų įrankis · Vilnius 2027 · ManoMiestas</title>", text)

    def test_register_page_uses_pending_semantics(self):
        text = (DIST / "register_2027.html").read_text("utf-8")
        self.assertIn("Informacija ruošiama", text)
        self.assertIn("Tai nėra teigiamas patvirtinimas", text)
        self.assertNotIn("programme_publications_prepared", text)
        self.assertNotIn("ballot_examples_prepared", text)

    def test_sources_page_contains_every_quote_and_non_scoreable_reason(self):
        text = (DIST / "sources_2027.html").read_text("utf-8")
        doc = json.loads((ROOT / "vaa" / "vilnius_2027_v21.json").read_text("utf-8"))
        quotes = [
            answer["quote"]
            for rows in doc["answers"].values()
            for answer in rows.values()
            if answer.get("quote")
        ]
        self.assertEqual(len(quotes), 32)
        for quote in quotes:
            self.assertIn(quote, text)
        for rows in doc["answers"].values():
            for answer in rows.values():
                if answer["stance"] != "unknown" and answer.get("scoreable") is False:
                    self.assertIn(answer["review_note"], text)

    def test_human_validation_package_is_linked_and_still_pending(self):
        sources = (DIST / "sources_2027.html").read_text("utf-8")
        methodology = (DIST / "methodology_2027.html").read_text("utf-8")
        self.assertIn("data/human_validation_v21.json", sources)
        self.assertIn("data/human_validation_v21.json", methodology)
        self.assertIn("Žmogaus peržiūra: pending", sources)
        self.assertIn("būsena: pending", methodology)
        human = json.loads((ROOT / "vaa" / "review" / "human_validation_v21.json").read_text("utf-8"))
        self.assertEqual(human["status"], "pending")
        self.assertFalse(human["public_use_allowed"])

    def test_no_forbidden_apis_or_storage(self):
        quiz = (DIST / "quiz_v21.html").read_text("utf-8")
        for bad in (
            "innerHTML",
            "insertAdjacentHTML",
            "document.write",
            "localStorage",
            "sessionStorage",
            "indexedDB",
            "XMLHttpRequest",
            "fetch(",
            "eval(",
        ):
            self.assertNotIn(bad, quiz, bad)
        for name in PAGES:
            text = (DIST / name).read_text("utf-8")
            self.assertNotIn("navigator.sendBeacon", text)
            self.assertNotIn("new WebSocket", text)

    def test_csp_closes_the_default_and_uses_inline_only(self):
        quiz = (DIST / "quiz_v21.html").read_text("utf-8")
        self.assertIn(
            "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:",
            quiz,
        )
        for name in ("index.html", "register_2027.html", "sources_2027.html", "methodology_2027.html", "ballot_2027.html"):
            text = (DIST / name).read_text("utf-8")
            self.assertIn("default-src 'none'; style-src 'unsafe-inline'; img-src data:", text)

    def test_all_links_are_https_or_existing_local_files(self):
        for name in PAGES:
            parser = LinkParser()
            parser.feed((DIST / name).read_text("utf-8"))
            for href in parser.links:
                if href.startswith("https://"):
                    continue
                if href.startswith("#"):
                    self.assertIn(href[1:], parser.ids, f"{name}: broken anchor {href}")
                    continue
                self.assertFalse(href.startswith("http://"), f"{name}: insecure link {href}")
                self.assertFalse(href.startswith("javascript:"), f"{name}: javascript link {href}")
                target = (DIST / href.split("#", 1)[0]).resolve()
                self.assertTrue(target.is_file(), f"{name}: missing local link {href}")

    def test_no_personal_or_research_paths_leak_into_public_output(self):
        for name in PAGES:
            text = (DIST / name).read_text("utf-8")
            for marker in ("/Users/", ".cache", "/tmp/", "palace.yaml", ".claude", ".freebuff", "docs/superpowers"):
                self.assertNotIn(marker, text, f"{name}: {marker}")

    def test_embedded_data_matches_curated_dataset_with_current_register_mayors(self):
        text = (DIST / "quiz_v21.html").read_text("utf-8")
        match = re.search(r'<script id="app-data" type="application/json">(.*?)</script>', text, re.S)
        self.assertIsNotNone(match)
        data = json.loads((ROOT / "vaa" / "vilnius_2027_v21.json").read_text("utf-8"))
        register = json.loads((ROOT / "vaa" / "vilnius_2027_register.json").read_text("utf-8"))
        self.assertEqual(json.loads(match.group(1)), sync_party_mayors(data, register))

    def test_all_agree_evidence_limitation_is_disclosed(self):
        quiz = (DIST / "quiz_v21.html").read_text("utf-8")
        methodology = (DIST / "methodology_2027.html").read_text("utf-8")
        self.assertIn("įskaitomi 2027 m. programų pozicijų įrašai yra „sutinku“", quiz)
        self.assertIn("Viena pozicijų kryptis", methodology)
        self.assertIn("nėra įskaitomos priešingos pozicijos", methodology)

    def test_ballot_page_uses_current_law_not_2023_practice(self):
        text = (DIST / "ballot_2027.html").read_text("utf-8")
        self.assertIn("Rinkimų kodekso 143 straipsnį", text)
        self.assertIn("iki 5 pasirinktų kandidatų", text)
        self.assertIn("2–5 kandidatai", text)
        self.assertIn("Oficialūs biuletenių pavyzdžiai dar nepaskelbti", text)
        self.assertNotIn("2023 m. savivaldybių rinkimuose buvo galima skirti iki 5", text)

    def test_pages_use_snapshot_date_and_variable_counts(self):
        register = json.loads((ROOT / "vaa" / "vilnius_2027_register.json").read_text("utf-8"))
        snapshot = register["snapshot"]["date"]
        for name in ("index.html", "register_2027.html"):
            text = (DIST / name).read_text("utf-8")
            self.assertIn(snapshot, text, name)
            self.assertNotIn("2026-10-26", text, name)
        index = (DIST / "index.html").read_text("utf-8")
        self.assertNotIn("Keturi sąrašai", index)
        self.assertNotIn("keturi sąrašų", index)
        self.assertNotIn("mero kandidatas", index)
        self.assertIn(f"{register['vilnius']['list_participants_count']} sąrašai", index)

    def test_sources_page_displays_structured_interpretation(self):
        text = (DIST / "sources_2027.html").read_text("utf-8")
        for label in (
            "Ar citata pagrindžia visą teiginį",
            "Lygmuo",
            "Dokumento statusas",
            "Kalbėtojo autoritetas",
            "Sąlygos",
            "Kaina ir kompromisai",
            "Ribos",
            "Žmogaus patvirtinta",
        ):
            self.assertIn(label, text)
        doc = json.loads((ROOT / "vaa" / "vilnius_2027_v21.json").read_text("utf-8"))
        limitations = {
            answer["interpretation"]["limitations"]
            for rows in doc["answers"].values()
            for answer in rows.values()
        }
        for limitation in list(limitations)[:10]:
            self.assertIn(limitation, text)

    def test_law_citations_are_present(self):
        methodology = (DIST / "methodology_2027.html").read_text("utf-8")
        ballot = (DIST / "ballot_2027.html").read_text("utf-8")
        for text in (methodology, ballot):
            self.assertIn("Rinkimų kodekso 143", text)
            self.assertIn("e-tar.lt", text)
        self.assertIn("158 straipsnį", ballot)
        self.assertIn("171 straipsnį", ballot)
        self.assertIn("168 straipsnio 4 dalį", ballot)

    def test_programme_similarity_limitations_are_disclosed(self):
        quiz = (DIST / "quiz_v21.html").read_text("utf-8")
        methodology = (DIST / "methodology_2027.html").read_text("utf-8")
        for text in (quiz, methodology):
            self.assertIn("strateginio balsavimo", text)
            self.assertIn("mandatų", text)
            self.assertIn("koalicij", text)
        self.assertIn("programų tekstų atitikimas", quiz)

    def test_every_quoted_row_has_source_context(self):
        doc = json.loads((ROOT / "vaa" / "vilnius_2027_v21.json").read_text("utf-8"))
        quoted = [
            (party_id, statement_id, answer)
            for party_id, rows in doc["answers"].items()
            for statement_id, answer in rows.items()
            if answer.get("quote")
        ]
        self.assertEqual(len(quoted), 32)
        for party_id, statement_id, answer in quoted:
            context = answer.get("source_context", "")
            self.assertTrue(context.strip(), f"{party_id}/{statement_id} has no context")
            self.assertGreaterEqual(len(context), 80, f"{party_id}/{statement_id} context too short")
        review = (DIST / "review_2027.html").read_text("utf-8")
        for _, _, answer in quoted:
            self.assertIn(answer["quote"], review)

    def test_review_workbench_never_signs_off(self):
        review = (DIST / "review_2027.html").read_text("utf-8")
        self.assertEqual(review.count('class="review-card"'), 32)
        self.assertIn("human_approved: false", review)
        self.assertIn("signoff: null", review)
        self.assertIn("draft: true", review)
        self.assertIn("Neįskaityta", review)
        self.assertNotIn('"human_approved": true', review)

    def test_compare_page_defaults_to_two_different_lists(self):
        compare = (DIST / "compare_2027.html").read_text("utf-8")
        payload = embedded_json(compare, "compare-data")
        self.assertEqual(payload["format"], "vaa-compare-workbench-v1")
        self.assertEqual(len(payload["lists"]), 4)
        self.assertEqual(len(payload["statements"]), 13)
        self.assertIn('<option value="tslkd" selected>', compare)
        self.assertIn('<option value="lsdp" selected>', compare)
        self.assertEqual(payload["lists"][0]["id"], "tslkd")
        self.assertEqual(payload["lists"][-1]["id"], "lsdp")
        for party in payload["lists"]:
            for statement in payload["statements"]:
                self.assertIn(statement["id"], payload["cells"][party["id"]])
        self.assertIn("data-head=", compare)

    def test_voter_guide_uses_official_dates_and_law(self):
        voter = (DIST / "rinkejui_2027.html").read_text("utf-8")
        for marker in (
            "2027 m. sausio 6 d.",
            "2027 m. vasario 5 d.",
            "2027 m. kovo 2 d.",
            "2027 m. kovo 3 d.",
            "2027 m. kovo 21 d.",
            "Rinkimų kodekso 8",
            "Rinkimų kodekso 9",
            "Rinkimų kodekso 62",
            "Rinkimų kodekso 138",
            "Rinkimų kodekso 139",
            "Rinkimų kodekso 142",
            "143 straipsnį",
        ):
            self.assertIn(marker, voter, marker)
        self.assertIn("VRK 2027 m. rinkimų grafikas", voter)
        self.assertIn("ne teisinė konsultacija", voter)

    def test_quiz_has_a_no_javascript_fallback(self):
        quiz = (DIST / "quiz_v21.html").read_text("utf-8")
        self.assertIn("<noscript>", quiz)
        self.assertIn("reikalauja JavaScript", quiz)
        self.assertIn("sources_2027.html", quiz)

    def test_every_table_has_a_caption(self):
        for name in PAGES:
            text = (DIST / name).read_text("utf-8")
            self.assertEqual(
                text.count("<table"),
                text.count("<caption"),
                f"{name}: tables without captions",
            )


def embedded_json(page: str, script_id: str) -> dict:
    match = re.search(
        rf'<script id="{re.escape(script_id)}" type="application/json">(.*?)</script>',
        page,
        re.S,
    )
    if not match:
        raise AssertionError(f"missing embedded JSON {script_id}")
    return json.loads(match.group(1))


if __name__ == "__main__":
    unittest.main()
