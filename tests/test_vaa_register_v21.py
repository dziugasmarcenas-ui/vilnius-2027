"""Offline tests for the official VRK 2027 participant register importer (v2)."""
import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from vaa_register_v21 import (  # noqa: E402
    RegisterError,
    apply_candidate_lists,
    parse_calendar,
    parse_index,
    parse_publication_page,
    validate_register,
)

FIXTURE = ROOT / "fixtures" / "vrk_2027_dalyviai.html"
REGISTER = ROOT / "vaa" / "vilnius_2027_register.json"


def register() -> dict:
    return json.loads(REGISTER.read_text("utf-8"))


@unittest.skipUnless(FIXTURE.is_file(), "private VRK HTML fixture is not part of the public source tree")
class ParseIndexTest(unittest.TestCase):
    def test_fixture_parses_every_participant_block_and_four_vilnius_lists(self):
        parsed = parse_index(FIXTURE.read_text("utf-8"))
        # The fixture is a private, refreshable VRK snapshot.  Assert internal
        # consistency and the known minimum instead of freezing a count that
        # legitimately changes while campaign registration is open.
        self.assertEqual(parsed["participants_count"], len(parsed["participants"]))
        self.assertGreaterEqual(parsed["participants_count"], 52)
        self.assertEqual(
            [row["number"] for row in parsed["participants"]],
            list(range(1, parsed["participants_count"] + 1)),
        )
        self.assertEqual({row["party_id"] for row in parsed["vilnius_lists"]}, {"tslkd", "lp", "ls", "lsdp"})
        self.assertEqual(
            sum(1 for row in parsed["vilnius_lists"] if row.get("mayor")),
            2,
        )

    def test_two_mayor_campaign_participants_are_identified(self):
        parsed = parse_index(FIXTURE.read_text("utf-8"))
        mayors = {
            row["party_id"]: row["mayor"]["name"]
            for row in parsed["vilnius_lists"]
            if row.get("mayor")
        }
        self.assertEqual(mayors, {"lp": "VYTAUTAS MITALAS", "ls": "SIMONAS GENTVILAS"})
        for row in parsed["vilnius_lists"]:
            if row.get("mayor"):
                self.assertNotIn("kandidatų sąrašas", row["mayor"]["label"].casefold())

    def test_vilnius_filter_does_not_accept_other_apygardos(self):
        parsed = parse_index(FIXTURE.read_text("utf-8"))
        for row in parsed["vilnius_lists"]:
            for key in ("list", "mayor"):
                entry = row.get(key)
                if entry:
                    self.assertIn("Nr. 57", entry["label"])


class PublicationPageTest(unittest.TestCase):
    GLOBAL_HEADER = (
        '<header><a href="https://vrk.lt/fs/prd/p/2026-10/Rinkimu%20grafikas%202027.pdf">'
        "Rinkimų grafikas</a></header>"
    )

    def page(self, content: str) -> str:
        return (
            self.GLOBAL_HEADER
            + '<div class="main__container-content"><article>'
            + content
            + "</article></div></main>"
        )

    def test_pending_page_ignores_global_header_pdf(self):
        state = parse_publication_page(
            self.page("<div>Informacija ruo&scaron;iama</div>")
        )
        self.assertEqual(state["status"], "pending")
        self.assertFalse(state["published"])
        self.assertTrue(state["pending_marker"])
        self.assertEqual(state["documents"], [])

    def test_published_page_finds_document_in_content_region(self):
        state = parse_publication_page(
            self.page(
                '<a href="/documents/programos.pdf">Kandidatų programos</a>'
            )
        )
        self.assertEqual(state["status"], "published")
        self.assertTrue(state["published"])
        self.assertEqual(
            state["documents"],
            [{"title": "Kandidatų programos", "url": "https://www.vrk.lt/documents/programos.pdf"}],
        )

    def test_missing_content_region_is_unknown_not_published(self):
        state = parse_publication_page(self.GLOBAL_HEADER + "<main></main>")
        self.assertEqual(state["status"], "unknown")
        self.assertFalse(state["published"])


class CalendarTest(unittest.TestCase):
    TEXT = """
    2026 10 28
    Baigiamas savarankiškų politinės kampanijos dalyvių registravimas.
    2026 12 17
    Baigiamas politinių komitetų ir kandidatų pareiškinių dokumentų pateikimas
    savivaldybės rinkimų komisijoje.
    2027 02 04
    Paskutinė diena, kai VRK priima sprendimą registruoti kandidatą
    kandidatų sąrašą arba jų neregistruoti.
    2027 02 05
    VRK savo interneto svetainėje paskelbia kandidatų sąrašus, kandidatus, jų
    kandidatų sąrašų rinkimų numerius
    """

    def test_official_dates_are_parsed_from_adjacent_calendar_phrases(self):
        status = parse_calendar(self.TEXT)
        self.assertEqual(status["participant_registration_ends"]["date"], "2026-10-28")
        self.assertEqual(status["candidate_nomination_ends"]["date"], "2026-12-17")
        self.assertEqual(status["candidate_lists_decision_by"]["date"], "2027-02-04")
        self.assertEqual(status["candidate_lists_published_by"]["date"], "2027-02-05")
        for row in status.values():
            self.assertTrue(row["source_url"].startswith("https://"))

    def test_missing_evidence_is_not_guessed(self):
        with self.assertRaisesRegex(RegisterError, "calendar date missing"):
            parse_calendar("2027 02 05\nVRK paskelbia kandidatų sąrašus")


class RegisterDocumentTest(unittest.TestCase):
    def setUp(self):
        self.doc = register()

    def test_checked_in_document_is_valid(self):
        validate_register(self.doc)

    def test_register_is_a_participant_register_not_a_ballot(self):
        status = self.doc["official_status"]
        self.assertEqual(self.doc["format"], "vaa-vrk-2027-register-v2")
        self.assertIs(status["candidate_lists_published"], False)
        self.assertIs(status["candidate_names_published"], False)
        self.assertIs(status["ballot_numbers_published"], False)
        self.assertEqual(status["candidate_lists_published_by"]["date"], "2027-02-05")
        self.assertEqual(self.doc["vilnius"]["list_participants_count"], 4)
        self.assertEqual(self.doc["vilnius"]["mayor_participants_count"], 2)
        snapshot = self.doc["snapshot"]
        self.assertEqual(snapshot["date"], self.doc["generated_at"])
        self.assertIs(snapshot["immutable"], True)
        self.assertEqual(snapshot["date"], snapshot["index_retrieved_at"][:10])

    def test_publication_pages_are_pending_not_prepared(self):
        status = self.doc["official_status"]
        self.assertIs(status["programme_publications_published"], False)
        self.assertEqual(status["programme_publications_status"], "pending")
        self.assertIn("Informacija ruošiama", status["programme_publications_note"])
        self.assertEqual(status["programme_publications_documents"], [])
        self.assertIs(status["ballot_examples_published"], False)
        self.assertEqual(status["ballot_examples_status"], "pending")
        self.assertIn("Informacija ruošiama", status["ballot_examples_note"])
        self.assertEqual(status["ballot_examples_documents"], [])

    def test_every_list_is_a_campaign_participant_without_invented_names_or_numbers(self):
        for row in self.doc["vilnius"]["lists"]:
            self.assertTrue(row["registered_on"].startswith("2026-"))
            self.assertTrue(row["organisation_name"])
            self.assertIsNone(row["ballot_number"])
            self.assertIs(row["candidate_names_published"], False)
            self.assertTrue(row["detail_url"].startswith("https://www.vrk.lt/"))
            if row["mayor_candidate"]:
                self.assertTrue(row["mayor_candidate"]["name"])
                self.assertIs(row["mayor_candidate"]["candidate_registered"], False)

    def test_flexible_list_count_is_accepted(self):
        doc = deepcopy(self.doc)
        doc["vilnius"]["lists"] = doc["vilnius"]["lists"][:3]
        doc["vilnius"]["list_participants_count"] = 3
        doc["vilnius"]["mayor_participants_count"] = sum(
            1 for row in doc["vilnius"]["lists"] if row.get("mayor_candidate")
        )
        validate_register(doc)

    def test_duplicate_list_id_is_rejected(self):
        doc = deepcopy(self.doc)
        doc["vilnius"]["lists"].append(deepcopy(doc["vilnius"]["lists"][0]))
        doc["vilnius"]["list_participants_count"] = len(doc["vilnius"]["lists"])
        with self.assertRaisesRegex(RegisterError, "duplicate list_id"):
            validate_register(doc)

    def test_ballot_number_claim_is_rejected(self):
        doc = deepcopy(self.doc)
        doc["vilnius"]["lists"][0]["ballot_number"] = 1
        with self.assertRaisesRegex(RegisterError, "ballot number"):
            validate_register(doc)

    def test_candidate_name_claim_is_rejected(self):
        doc = deepcopy(self.doc)
        doc["vilnius"]["lists"][0]["candidate_names_published"] = True
        with self.assertRaisesRegex(RegisterError, "candidate names"):
            validate_register(doc)

    def test_published_programmes_claim_is_rejected(self):
        doc = deepcopy(self.doc)
        doc["official_status"]["programme_publications_published"] = True
        with self.assertRaisesRegex(RegisterError, "programme_publications"):
            validate_register(doc)

    def test_published_ballot_examples_claim_is_rejected(self):
        doc = deepcopy(self.doc)
        doc["official_status"]["ballot_examples_published"] = True
        with self.assertRaisesRegex(RegisterError, "ballot_examples"):
            validate_register(doc)

    def test_bad_election_identity_is_rejected(self):
        doc = deepcopy(self.doc)
        doc["election"]["apygarda"] = 56
        with self.assertRaisesRegex(RegisterError, "apygarda"):
            validate_register(doc)
        doc = deepcopy(self.doc)
        doc["election"]["date"] = "2027-03-08"
        with self.assertRaisesRegex(RegisterError, "date"):
            validate_register(doc)

    def test_source_hash_is_required(self):
        doc = deepcopy(self.doc)
        doc["source"]["index_sha256"] = ""
        with self.assertRaisesRegex(RegisterError, "index hash"):
            validate_register(doc)


class CandidateListOverlayTest(unittest.TestCase):
    def setUp(self):
        self.doc = register()

    def overlay(self) -> dict:
        lists = []
        for index, row in enumerate(self.doc["vilnius"]["lists"]):
            item = {
                "list_id": row["list_id"],
                "ballot_number": index + 1,
                "candidates": [
                    {"name": "Kandidatas A", "number": 1},
                    {"name": "Kandidatas B", "number": 2},
                ],
            }
            if row.get("mayor_candidate"):
                item["mayor_candidate"] = {"ballot_number": index + 1}
            lists.append(item)
        return {
            "format": "vaa-candidate-lists-2027-v1",
            "status": "published",
            "published_at": "2027-02-05",
            "source_url": "https://www.vrk.lt/2027-kandidatu-sarasai",
            "lists": lists,
        }

    def test_unpublished_overlay_leaves_candidate_fields_empty(self):
        overlay = {
            "format": "vaa-candidate-lists-2027-v1",
            "status": "not_published",
            "lists": [],
        }
        doc = apply_candidate_lists(deepcopy(self.doc), overlay)
        self.assertIs(doc["official_status"]["candidate_lists_published"], False)
        for row in doc["vilnius"]["lists"]:
            self.assertIsNone(row["ballot_number"])
            self.assertEqual(row["candidates"], [])

    def test_published_overlay_adds_names_and_numbers(self):
        doc = apply_candidate_lists(deepcopy(self.doc), self.overlay())
        self.assertIs(doc["official_status"]["candidate_lists_published"], True)
        self.assertTrue(doc["official_status"]["candidate_lists_source_url"].startswith("https://"))
        for row in doc["vilnius"]["lists"]:
            self.assertTrue(row["candidate_list_published"])
            self.assertTrue(row["candidate_names_published"])
            self.assertGreaterEqual(row["ballot_number"], 1)
            self.assertEqual(len(row["candidates"]), 2)
        validate_register(doc)

    def test_overlay_without_source_is_rejected(self):
        overlay = self.overlay()
        overlay["source_url"] = ""
        with self.assertRaisesRegex(RegisterError, "source_url"):
            apply_candidate_lists(deepcopy(self.doc), overlay)

    def test_overlay_with_incomplete_coverage_is_rejected(self):
        overlay = self.overlay()
        overlay["lists"] = overlay["lists"][:1]
        with self.assertRaisesRegex(RegisterError, "does not cover"):
            apply_candidate_lists(deepcopy(self.doc), overlay)

    def test_overlay_cannot_invent_unknown_list(self):
        overlay = self.overlay()
        overlay["lists"][0]["list_id"] = "not-a-list"
        with self.assertRaisesRegex(RegisterError, "unknown list"):
            apply_candidate_lists(deepcopy(self.doc), overlay)


if __name__ == "__main__":
    unittest.main()
