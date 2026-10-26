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

from vaa_build_v21 import DIST, PAGES, build, check  # noqa: E402


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

    def test_embedded_data_matches_curated_dataset(self):
        text = (DIST / "quiz_v21.html").read_text("utf-8")
        match = re.search(r'<script id="app-data" type="application/json">(.*?)</script>', text, re.S)
        self.assertIsNotNone(match)
        self.assertEqual(
            json.loads(match.group(1)),
            json.loads((ROOT / "vaa" / "vilnius_2027_v21.json").read_text("utf-8")),
        )

    def test_all_agree_evidence_limitation_is_disclosed(self):
        quiz = (DIST / "quiz_v21.html").read_text("utf-8")
        methodology = (DIST / "methodology_2027.html").read_text("utf-8")
        self.assertIn("įskaitomi 2026-10-26 pozicijų įrašai yra „sutinku“", quiz)
        self.assertIn("Viena pozicijų kryptis", methodology)
        self.assertIn("nėra įskaitomos priešingos pozicijos", methodology)

    def test_ballot_page_labels_preference_votes_as_preliminary(self):
        text = (DIST / "ballot_2027.html").read_text("utf-8")
        self.assertIn("2023 m. savivaldybių rinkimuose buvo galima skirti iki 5 pirmumo balsų", text)
        self.assertIn("2027 m. tvarka turi būti patvirtinta oficialiame biuletenyje", text)
        self.assertIn("Oficialūs biuletenių pavyzdžiai dar nepaskelbti", text)


if __name__ == "__main__":
    unittest.main()
