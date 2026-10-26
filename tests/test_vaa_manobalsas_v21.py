"""Tests that Mano Balsas remains clearly labelled context, not a 2027 score input."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from vaa_manobalsas_v21 import METHOD_QUOTES, validate  # noqa: E402

MB = ROOT / "vaa" / "manobalsas_2024_context.json"


class ManoBalsasContextTest(unittest.TestCase):
    def setUp(self):
        self.doc = json.loads(MB.read_text("utf-8"))

    def test_document_validates(self):
        validate(self.doc)

    def test_status_is_context_only(self):
        self.assertEqual(self.doc["status"], "context_only")
        self.assertIn("nėra 2027", self.doc["scope_note"])
        self.assertIn("nenaudojamos", self.doc["scope_note"])

    def test_2024_test_shape_is_recorded_without_party_positions(self):
        test = self.doc["test"]
        self.assertEqual(test["question_count"], 45)
        self.assertEqual(
            test["dimensions"],
            ["Rinka ir valstybė", "Visuomenė", "Ekologija", "Užsienio ir gynybos politika", "Kiti klausimai"],
        )
        self.assertNotIn("answers", self.doc)
        self.assertNotIn("parties", self.doc)
        self.assertNotIn("stances", self.doc)

    def test_methodology_quotes_are_hashed_and_sourced(self):
        for quote in self.doc["methodology"]["quotes"]:
            self.assertTrue(quote["quote"])
            self.assertRegex(quote["quote_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(quote["source_id"], "manobalsas_methodology")
            self.assertIn(quote["id"], {item["id"] for item in METHOD_QUOTES})

    def test_use_boundary_is_explicit(self):
        use = self.doc["use_in_v21"]
        self.assertIn("questionnaire structure", use["used_for"])
        self.assertIn("2027 Vilnius party positions", use["not_used_for"])
        self.assertIn("national 2024 coordinates as local promises", use["not_used_for"])

    def test_both_sources_and_limits_are_present(self):
        self.assertEqual(len(self.doc["sources"]), 2)
        for source in self.doc["sources"]:
            self.assertTrue(source["url"].startswith("https://"))
            self.assertEqual(source["http_status"], 200)
            self.assertRegex(source["content_sha256"], r"^[0-9a-f]{64}$")
            self.assertRegex(source["text_sha256"], r"^[0-9a-f]{64}$")
        self.assertGreaterEqual(len(self.doc["limits"]), 3)

    def test_no_2027_claim_in_titles(self):
        self.assertIn("2024", self.doc["test"]["title"])
        self.assertIn("2024", self.doc["methodology"]["title"])
        self.assertNotIn("2027", self.doc["test"]["title"])
        self.assertNotIn("2027", self.doc["methodology"]["title"])


if __name__ == "__main__":
    unittest.main()
