"""Offline tests for the official VRK 2027 participant register importer."""
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
    parse_calendar,
    parse_index,
    validate_register,
)

FIXTURE = ROOT / "fixtures" / "vrk_2027_dalyviai.html"
REGISTER = ROOT / "vaa" / "vilnius_2027_register.json"


def register() -> dict:
    return json.loads(REGISTER.read_text("utf-8"))


@unittest.skipUnless(FIXTURE.is_file(), "private VRK HTML fixture is not part of the public source tree")
class ParseIndexTest(unittest.TestCase):
    def test_fixture_has_52_participants_and_four_vilnius_lists(self):
        parsed = parse_index(FIXTURE.read_text("utf-8"))
        self.assertEqual(parsed["participants_count"], 52)
        self.assertEqual({row["party_id"] for row in parsed["vilnius_lists"]}, {"tslkd", "lp", "ls", "lsdp"})
        self.assertEqual(
            sum(1 for row in parsed["vilnius_lists"] if row.get("mayor")),
            2,
        )

    def test_two_registered_mayors_are_identified(self):
        parsed = parse_index(FIXTURE.read_text("utf-8"))
        mayors = {
            row["party_id"]: row["mayor"]["name"]
            for row in parsed["vilnius_lists"]
            if row.get("mayor")
        }
        self.assertEqual(mayors, {"lp": "VYTAUTAS MITALAS", "ls": "SIMONAS GENTVILAS"})

    def test_vilnius_filter_does_not_accept_other_apygardos(self):
        parsed = parse_index(FIXTURE.read_text("utf-8"))
        for row in parsed["vilnius_lists"]:
            for key in ("list", "mayor"):
                entry = row.get(key)
                if entry:
                    self.assertIn("Nr. 57", entry["label"])


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
        self.assertIs(status["candidate_lists_published"], False)
        self.assertIs(status["candidate_names_published"], False)
        self.assertIs(status["ballot_numbers_published"], False)
        self.assertEqual(status["candidate_lists_published_by"]["date"], "2027-02-05")
        self.assertEqual(self.doc["vilnius"]["registered_lists_count"], 4)
        self.assertEqual(self.doc["vilnius"]["registered_mayor_candidates_count"], 2)

    def test_publication_pages_are_pending_not_prepared(self):
        status = self.doc["official_status"]
        self.assertIs(status["programme_publications_published"], False)
        self.assertEqual(status["programme_publications_status"], "pending")
        self.assertIn("Informacija ruošiama", status["programme_publications_note"])
        self.assertIs(status["ballot_examples_published"], False)
        self.assertEqual(status["ballot_examples_status"], "pending")
        self.assertIn("Informacija ruošiama", status["ballot_examples_note"])

    def test_every_list_is_registered_and_has_no_invented_names_or_numbers(self):
        for row in self.doc["vilnius"]["lists"]:
            self.assertTrue(row["registered_on"].startswith("2026-"))
            self.assertIsNone(row["ballot_number"])
            self.assertIs(row["candidate_names_published"], False)
            self.assertTrue(row["detail_url"].startswith("https://www.vrk.lt/"))
            if row["mayor_candidate"]:
                self.assertTrue(row["mayor_candidate"]["name"])

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
        with self.assertRaisesRegex(RegisterError, "programme publications"):
            validate_register(doc)

    def test_published_ballot_examples_claim_is_rejected(self):
        doc = deepcopy(self.doc)
        doc["official_status"]["ballot_examples_published"] = True
        with self.assertRaisesRegex(RegisterError, "ballot examples"):
            validate_register(doc)

    def test_missing_list_and_duplicate_list_are_rejected(self):
        doc = deepcopy(self.doc)
        doc["vilnius"]["lists"] = doc["vilnius"]["lists"][:3]
        with self.assertRaisesRegex(RegisterError, "expected the four"):
            validate_register(doc)
        doc = deepcopy(self.doc)
        doc["vilnius"]["lists"].append(deepcopy(doc["vilnius"]["lists"][0]))
        with self.assertRaisesRegex(RegisterError, "duplicate party list"):
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


if __name__ == "__main__":
    unittest.main()
