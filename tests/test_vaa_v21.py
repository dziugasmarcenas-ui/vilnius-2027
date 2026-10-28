"""Tests for the v21 2027 Vilnius dataset, evidence gates and scoring engine."""
import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from vaa_v21 import (  # noqa: E402
    CHECKS,
    MIN_RECOMMEND_SCORE,
    VaaError,
    check_evidence,
    load_data,
    score_v21,
    validate_data,
)

DATA = ROOT / "vaa" / "vilnius_2027_v21.json"
REGISTER = ROOT / "vaa" / "vilnius_2027_register.json"
SOURCES = ROOT / "vaa" / "sources_2027_v21.json"
MB = ROOT / "vaa" / "manobalsas_2024_context.json"


def data() -> dict:
    return json.loads(DATA.read_text("utf-8"))


def selection(topics=None, top_topic=None, answers=None) -> dict:
    return {
        "selected_topics": list(topics or []),
        "top_topic": top_topic,
        "answers": dict(answers or {}),
    }


class DatasetTest(unittest.TestCase):
    def test_real_dataset_validates(self):
        validate_data(data())

    def test_shape_and_evidence_counts_are_exact(self):
        doc = data()
        self.assertEqual(len(doc["topics"]), 6)
        self.assertEqual(len(doc["statements"]), 13)
        self.assertEqual({p["id"] for p in doc["parties"]}, {"tslkd", "lp", "ls", "lsdp"})
        known = [a for rows in doc["answers"].values() for a in rows.values() if a["stance"] != "unknown"]
        scoreable = [a for rows in doc["answers"].values() for a in rows.values() if a.get("scoreable")]
        self.assertEqual(len(known), 32)
        self.assertEqual(len(scoreable), 26)
        self.assertEqual(len(known) - len(scoreable), 6)

    def test_every_coded_position_is_current_and_attributed(self):
        doc = data()
        for party_id, rows in doc["answers"].items():
            for statement_id, answer in rows.items():
                if answer["stance"] == "unknown":
                    self.assertTrue(answer.get("note"), f"{party_id}/{statement_id} has no gap note")
                    continue
                self.assertEqual(answer["election_year"], 2027)
                self.assertTrue(answer["source_url"].startswith("https://"))
                self.assertTrue(answer["source_date"].startswith("2026-"))
                self.assertIn(answer["source_type"], ("programme", "statement"))
                self.assertIn(answer["speaker"]["kind"], ("party", "person", "coalition", "council_group"))
                self.assertIn(answer["review_status"], ("assistant_checked", "human_approved"))

    def test_non_scoreable_rows_explain_the_failed_gate(self):
        doc = data()
        for party_id, rows in doc["answers"].items():
            for statement_id, answer in rows.items():
                if answer["stance"] == "unknown":
                    continue
                if answer["scoreable"] is True:
                    self.assertIs(answer["full_proposition"], True)
                    self.assertIs(answer["party_attribution"], True)
                else:
                    self.assertTrue(answer["review_note"])
                    self.assertTrue(
                        answer["full_proposition"] is False or answer["party_attribution"] is False,
                        f"{party_id}/{statement_id}: no failed gate recorded",
                    )

    def test_no_historical_material_is_presented_as_a_2027_position(self):
        doc = data()
        for rows in doc["answers"].values():
            for answer in rows.values():
                if answer["stance"] == "unknown":
                    continue
                self.assertNotIn("2023", answer["source_date"])
                self.assertNotIn("2024", answer["source_date"])
        self.assertEqual(doc["context"]["manobalsas"]["status"], "context_only")

    def test_human_validation_is_still_pending(self):
        doc = data()
        self.assertEqual(doc["human_validation"]["status"], "pending")
        self.assertEqual(doc["human_validation"]["approved_rows"], 0)
        self.assertFalse(doc["human_validation"].get("reviewers"))
        self.assertEqual(
            sum(1 for rows in doc["answers"].values() for a in rows.values() if a.get("review_status") == "human_approved"),
            0,
        )

    def test_no_ballot_or_candidate_number_claims(self):
        reg = json.loads(REGISTER.read_text("utf-8"))
        status = reg["official_status"]
        self.assertIs(status["candidate_lists_published"], False)
        self.assertIs(status["candidate_names_published"], False)
        self.assertIs(status["ballot_numbers_published"], False)
        self.assertIs(status["programme_publications_published"], False)
        self.assertEqual(status["programme_publications_status"], "pending")
        self.assertIs(status["ballot_examples_published"], False)
        self.assertEqual(status["ballot_examples_status"], "pending")
        for row in reg["vilnius"]["lists"]:
            self.assertIsNone(row["ballot_number"])
            self.assertIs(row["candidate_names_published"], False)

    def test_source_registry_is_https_and_current_or_context(self):
        doc = json.loads(SOURCES.read_text("utf-8"))
        self.assertEqual(doc["format"], "vaa-source-registry-v21")
        ids = [row["id"] for row in doc["sources"]]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 10)
        for row in doc["sources"]:
            self.assertTrue(row["url"].startswith("https://"))
            self.assertIn(row["kind"], ("pdf", "html", "legal"))
        self.assertIn("manobalsas_methodology", ids)
        self.assertIn("manobalsas_test", ids)
        self.assertIn("rinkimu_kodeksas_2027", ids)

    def test_manobalsas_context_never_contains_party_positions(self):
        doc = json.loads(MB.read_text("utf-8"))
        self.assertEqual(doc["status"], "context_only")
        self.assertNotIn("answers", doc)
        self.assertNotIn("parties", doc)
        self.assertEqual(doc["test"]["question_count"], 45)
        self.assertEqual(len(doc["test"]["dimensions"]), 5)
        self.assertEqual(len(doc["methodology"]["quotes"]), 5)


class ScoreTest(unittest.TestCase):
    def setUp(self):
        self.doc = data()
        self.topics = [t["id"] for t in self.doc["topics"]]

    def score(self, answers, topics=None, top_topic=None):
        return score_v21(self.doc, selection(topics or self.topics, top_topic, answers))

    def test_all_agree_is_close_not_a_single_winner(self):
        result = self.score({s["id"]: 2 for s in self.doc["statements"]}, top_topic="transport")
        self.assertEqual(result["status"], "close")
        self.assertEqual(result["close_reason"], "margin")
        self.assertEqual(result["top"], "lsdp")
        self.assertEqual(result["lead"], 0)
        self.assertGreater(len(result["shortlist"]), 1)
        self.assertIsNone(result["plan"]["council_list"])

    def test_all_disagree_triggers_no_match_not_a_zero_score_recommendation(self):
        result = self.score({s["id"]: -2 for s in self.doc["statements"]}, top_topic="transport")
        self.assertEqual(result["status"], "no_match")
        self.assertLess(result["parties"][0]["score"], MIN_RECOMMEND_SCORE)
        self.assertIsNone(result["plan"]["council_list"])
        self.assertEqual(result["shortlist"], [])
        self.assertEqual(result["plan"]["shortlist"], [])
        self.assertIn("nesiekia 50/100", " ".join(result["plan"]["notes"]))

    def test_no_answers_is_insufficient(self):
        result = self.score({})
        self.assertEqual(result["status"], "insufficient")
        self.assertIsNone(result["top"])
        self.assertEqual(result["plan"]["council_list"], None)
        self.assertEqual(result["plan"]["shortlist"], [])
        for party in result["parties"]:
            self.assertIsNone(party["score"])
            self.assertFalse(party["eligible"])

    def test_one_answer_is_not_enough_but_two_are_enough(self):
        one = self.score({"rail_transit": 2}, ["transport"], "transport")
        self.assertEqual(one["status"], "insufficient")
        self.assertFalse(any(p["eligible"] for p in one["parties"]))
        two = self.score({"rail_transit": 2, "a_lanes": 2}, ["transport"], "transport")
        self.assertEqual(two["status"], "close")
        self.assertTrue(any(p["eligible"] for p in two["parties"]))

    def test_unknown_positions_do_not_lower_a_party_score(self):
        base_answers = {
            "rail_transit": 2,
            "kindergarten_places": 2,
            "all_day_school": 2,
            "housing_land_supply": 2,
        }
        base = score_v21(
            self.doc,
            selection(["transport", "education", "housing"], "transport", base_answers),
        )
        base_tslkd = next(p for p in base["parties"] if p["party_id"] == "tslkd")
        with_unknown = score_v21(
            self.doc,
            selection(
                ["transport", "education", "housing"],
                "transport",
                {**base_answers, "bike_network": 2},
            ),
        )
        extra_tslkd = next(p for p in with_unknown["parties"] if p["party_id"] == "tslkd")
        self.assertEqual(base_tslkd["score"], extra_tslkd["score"])
        self.assertEqual(base_tslkd["known"], extra_tslkd["known"])
        self.assertGreater(base_tslkd["coverage_pct"], extra_tslkd["coverage_pct"])

    def test_low_coverage_party_is_ineligible(self):
        result = self.score({"rail_transit": 2, "a_lanes": 2}, ["transport"], "transport")
        ls = next(p for p in result["parties"] if p["party_id"] == "ls")
        self.assertEqual(ls["known"], 0)
        self.assertIsNone(ls["score"])
        self.assertFalse(ls["eligible"])
        self.assertEqual(ls["coverage_pct"], 0)

    def test_clear_result_names_one_list_and_handles_missing_mayor(self):
        answers = {
            "rail_transit": 2,
            "a_lanes": 1,
            "bike_network": 0,
            "transit_affordability": -1,
            "housing_land_supply": 2,
            "civil_shelters": 1,
            "resilience_centers": -2,
        }
        result = score_v21(
            self.doc,
            selection(["transport", "housing", "safety"], "housing", answers),
        )
        self.assertEqual(result["status"], "clear")
        self.assertEqual(result["top"], "lsdp")
        self.assertEqual(result["plan"]["council_list"], "lsdp")
        self.assertIsNone(result["plan"]["mayor"])
        self.assertTrue(any("mero kampanijos dalyvio" in note for note in result["plan"]["notes"]))
        self.assertEqual(
            {row["name"] for row in result["plan"]["mayor_candidates"]},
            {"Vytautas Mitalas", "Simonas Gentvilas"},
        )

    def test_no_match_plan_still_lists_registered_mayor_candidates(self):
        result = self.score({s["id"]: -2 for s in self.doc["statements"]}, top_topic="transport")
        self.assertEqual(result["status"], "no_match")
        self.assertEqual(
            {row["name"] for row in result["plan"]["mayor_candidates"]},
            {"Vytautas Mitalas", "Simonas Gentvilas"},
        )

    def test_top_topic_falls_back_to_first_selected_topic(self):
        result = score_v21(
            self.doc,
            selection(["transport"], "education", {"rail_transit": 2, "a_lanes": 2}),
        )
        self.assertEqual(result["top_topic"], "transport")

    def test_close_coverage_result_has_no_single_list(self):
        answers = {
            s["id"]: [2, 1, 0, -1, -2][i % 5]
            for i, s in enumerate(self.doc["statements"])
        }
        result = self.score(answers, top_topic="economy")
        self.assertEqual(result["status"], "close")
        self.assertEqual(result["close_reason"], "coverage")
        self.assertEqual(len(result["shortlist"]), 1)
        self.assertIsNone(result["plan"]["council_list"])
        self.assertEqual(result["plan"]["shortlist"], result["shortlist"])

    def test_plan_is_explicitly_preliminary(self):
        result = self.score({s["id"]: 2 for s in self.doc["statements"]}, top_topic="transport")
        plan = result["plan"]
        self.assertEqual(plan["preference_votes"], 5)
        self.assertEqual(plan["final_lists_date"], "2027-02-05")
        self.assertIsNone(plan["ballot_number"])
        self.assertTrue(any("nepaskelbti" in note for note in plan["notes"]))
        self.assertTrue(any("ne partijos, kandidato ar koalicijos rekomendacija" in note for note in plan["notes"]))

    def test_invalid_answers_are_rejected(self):
        for value in (3, -3, 1.5, True, "2"):
            with self.subTest(value=value):
                with self.assertRaises(VaaError):
                    self.score({"rail_transit": value})
        with self.assertRaises(VaaError):
            score_v21(self.doc, {"selected_topics": "transport", "answers": {}})
        with self.assertRaises(VaaError):
            score_v21(self.doc, {"selected_topics": ["transport"], "answers": []})


class ValidationTest(unittest.TestCase):
    def test_bad_format_is_rejected(self):
        doc = data()
        doc["format"] = "other"
        with self.assertRaisesRegex(VaaError, "format"):
            validate_data(doc)

    def test_unknown_topic_and_missing_statement_are_rejected(self):
        doc = data()
        doc["statements"][0]["topic_id"] = "not-a-topic"
        with self.assertRaisesRegex(VaaError, "unknown topic"):
            validate_data(doc)
        doc = data()
        doc["statements"].pop()
        with self.assertRaisesRegex(VaaError, "13 statements"):
            validate_data(doc)

    def test_missing_party_and_missing_statement_rows_are_rejected(self):
        doc = data()
        doc["answers"].pop("lp")
        with self.assertRaisesRegex(VaaError, "four registered parties"):
            validate_data(doc)
        doc = data()
        doc["answers"]["lp"].pop("rail_transit")
        with self.assertRaisesRegex(VaaError, "answer set mismatch"):
            validate_data(doc)

    def test_unknown_row_needs_a_reason(self):
        doc = data()
        doc["answers"]["lp"]["transit_affordability"]["note"] = ""
        with self.assertRaisesRegex(VaaError, "unknown rows need a note"):
            validate_data(doc)

    def test_scoreable_row_cannot_fail_attribution(self):
        doc = data()
        doc["answers"]["lp"]["rail_transit"]["party_attribution"] = False
        with self.assertRaisesRegex(VaaError, "scoreable row needs party attribution"):
            validate_data(doc)

    def test_non_scoreable_row_must_name_a_failed_gate(self):
        doc = data()
        row = doc["answers"]["tslkd"]["a_lanes"]
        row["full_proposition"] = True
        row["party_attribution"] = True
        with self.assertRaisesRegex(VaaError, "failed gate"):
            validate_data(doc)

    def test_historical_or_http_source_is_rejected_for_coded_position(self):
        doc = data()
        doc["answers"]["lp"]["rail_transit"]["source_url"] = "http://laisvespartija.lt/transportas/"
        with self.assertRaisesRegex(VaaError, "https"):
            validate_data(doc)
        doc = data()
        doc["answers"]["lp"]["rail_transit"]["election_year"] = 2024
        with self.assertRaisesRegex(VaaError, "election_year must be 2027"):
            validate_data(doc)

    def test_approved_human_validation_requires_names_and_rows(self):
        doc = data()
        doc["human_validation"] = {"status": "approved", "approved_rows": 0, "reviewed_rows": 0}
        with self.assertRaisesRegex(VaaError, "reviewer names"):
            validate_data(doc)
        doc = data()
        doc["human_validation"] = {
            "status": "approved",
            "approved_rows": 32,
            "reviewed_rows": 32,
            "reviewers": ["Reviewer A"],
        }
        validate_data(doc)

    def test_quote_change_is_detected_by_source_check(self):
        doc = data()
        doc["answers"]["lp"]["rail_transit"]["quote"] += " (pakeista)"
        with self.assertRaisesRegex(VaaError, "quote changed since check"):
            check_evidence(doc)

    def test_source_check_covers_every_quoted_row(self):
        doc = data()
        checks = json.loads(CHECKS.read_text("utf-8"))
        quoted = {
            f"{party_id}__{statement_id}"
            for party_id, rows in doc["answers"].items()
            for statement_id, row in rows.items()
            if row.get("quote")
        }
        checked = {row["evidence_id"] for row in checks["checks"] if not row["evidence_id"].startswith("manobalsas_context__")}
        self.assertEqual(quoted, checked)


if __name__ == "__main__":
    unittest.main()
