"""Transition tests for the 2027 register.

These tests exercise the states the app must move through while the election
changes: a new Vilnius organisation appears, VRK publishes programme or ballot
documents, and VRK publishes final candidate lists with names and numbers.
They do not use the network and never invent official data.
"""
import json
import sys
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from vaa_build_v21 import index_content, register_content  # noqa: E402
from vaa_register_v21 import (  # noqa: E402
    RegisterError,
    apply_candidate_lists,
    parse_index,
    validate_register,
)

REGISTER = ROOT / "vaa" / "vilnius_2027_register.json"
DATA = ROOT / "vaa" / "vilnius_2027_v21.json"


def register() -> dict:
    return json.loads(REGISTER.read_text("utf-8"))


def dataset() -> dict:
    return json.loads(DATA.read_text("utf-8"))


def checks() -> dict:
    return {"checks": []}


def new_list_row() -> dict:
    return {
        "list_id": "org_99_naujoji_partija",
        "party_id": None,
        "organisation_id": "org_99_naujoji_partija",
        "organisation_name": "Naujoji partija",
        "organisation_kind": "Partija",
        "list_name": "Naujosios partijos Vilniaus miesto rinkimų apygardoje Nr. 57 kandidatų sąrašas",
        "participant_number": 99,
        "registered_on": "2026-10-28",
        "decision": "PK1-2027SAV-S999",
        "status": None,
        "detail_url": "https://www.vrk.lt/statiniai/puslapiai/politKamp/1884/dalyviai/org99.html",
        "registration_outcome": "registered",
        "mayor_candidate": None,
        "candidate_list_published": False,
        "candidate_names_published": False,
        "ballot_number": None,
        "candidates": [],
        "programme_publications": [],
    }


def add_new_list(doc: dict) -> dict:
    rows = doc["vilnius"]["lists"]
    rows.append(new_list_row())
    doc["vilnius"]["list_participants_count"] = len(rows)
    doc["vilnius"]["mayor_participants_count"] = sum(
        1 for row in rows if row.get("mayor_candidate")
    )
    return doc


def published_overlay(doc: dict) -> dict:
    items = []
    for index, row in enumerate(doc["vilnius"]["lists"]):
        item = {
            "list_id": row["list_id"],
            "ballot_number": index + 1,
            "candidates": [
                {"name": f"Kandidatas {index + 1}A", "number": 1},
                {"name": f"Kandidatas {index + 1}B", "number": 2},
            ],
        }
        if row.get("mayor_candidate"):
            item["mayor_candidate"] = {"ballot_number": index + 1}
        items.append(item)
    return {
        "format": "vaa-candidate-lists-2027-v1",
        "status": "published",
        "published_at": "2027-02-05",
        "source_url": "https://www.vrk.lt/2027-kandidatu-sarasai",
        "lists": items,
    }


class NewOrganisationTest(unittest.TestCase):
    SYNTHETIC_INDEX = """
    <li><b>99. Naujoji partija</b><br/>
    Partija,<br/>
    <a href='?srcUrl=/politKamp/1884/dalyviai/org99.html'>
    1. Naujosios partijos Vilniaus miesto rinkimų apygardoje Nr. 57 kandidatų sąrašas (S)</a>
    <a href='?srcUrl=/politKamp/1884/dalyviai/person99.html'>
    2. Naujosios partijos Vilniaus miesto rinkimų apygardoje Nr. 57 kandidatas JONAS JONAITIS (S)</a>
    </li>
    """

    def test_importer_discovers_an_arbitrary_new_vilnius_organisation(self):
        parsed = parse_index(self.SYNTHETIC_INDEX)
        self.assertEqual(parsed["participants_count"], 1)
        self.assertEqual(len(parsed["vilnius_lists"]), 1)
        row = parsed["vilnius_lists"][0]
        self.assertEqual(row["party_id"], None)
        self.assertEqual(row["organisation_id"], "org_99_naujoji_partija")
        self.assertEqual(row["list"]["label"].split(" (")[0].startswith("Naujosios"), True)
        self.assertEqual(row["mayor"]["name"], "JONAS JONAITIS")

    def test_validator_accepts_a_new_organisation_row(self):
        doc = add_new_list(register())
        validate_register(doc)
        self.assertEqual(
            doc["vilnius"]["list_participants_count"],
            len(doc["vilnius"]["lists"]),
        )

    def test_new_organisation_can_be_published_with_names_and_numbers(self):
        doc = add_new_list(register())
        doc = apply_candidate_lists(doc, published_overlay(doc))
        validate_register(doc)
        new_row = doc["vilnius"]["lists"][-1]
        self.assertTrue(new_row["candidate_list_published"])
        self.assertEqual(new_row["ballot_number"], len(doc["vilnius"]["lists"]))
        self.assertEqual(
            [candidate["name"] for candidate in new_row["candidates"]],
            [f"Kandidatas {len(doc['vilnius']['lists'])}A", f"Kandidatas {len(doc['vilnius']['lists'])}B"],
        )


class PublicationTransitionTest(unittest.TestCase):
    def test_programme_page_can_move_from_pending_to_published(self):
        doc = deepcopy(register())
        status = doc["official_status"]
        status["programme_publications_status"] = "published"
        status["programme_publications_published"] = True
        status["programme_publications_documents"] = [
            {"title": "Kandidatų programos", "url": "https://www.vrk.lt/programos.pdf"}
        ]
        validate_register(doc)
        text = register_content(dataset(), doc, checks())
        self.assertIn("paskelbti", text)
        self.assertIn("Kandidatų programos", text)

    def test_programme_page_cannot_claim_published_without_documents(self):
        doc = deepcopy(register())
        status = doc["official_status"]
        status["programme_publications_status"] = "published"
        status["programme_publications_published"] = True
        status["programme_publications_documents"] = []
        with self.assertRaisesRegex(RegisterError, "published state needs"):
            validate_register(doc)

    def test_pending_page_cannot_carry_documents(self):
        doc = deepcopy(register())
        doc["official_status"]["ballot_examples_documents"] = [
            {"title": "Biuletenis", "url": "https://www.vrk.lt/biuletenis.pdf"}
        ]
        with self.assertRaisesRegex(RegisterError, "documents cannot exist"):
            validate_register(doc)

    def test_ballot_examples_transition_is_visible_on_the_register_page(self):
        doc = deepcopy(register())
        status = doc["official_status"]
        status["ballot_examples_status"] = "published"
        status["ballot_examples_published"] = True
        status["ballot_examples_documents"] = [
            {"title": "Mero biuletenio pavyzdys", "url": "https://www.vrk.lt/mero-biuletenis.pdf"}
        ]
        validate_register(doc)
        text = register_content(dataset(), doc, checks())
        self.assertIn("Mero biuletenio pavyzdys", text)


class CandidatePublicationTransitionTest(unittest.TestCase):
    def test_register_page_does_not_show_names_before_publication(self):
        text = register_content(dataset(), register(), checks())
        self.assertIn("nepaskelbtos", text)
        self.assertIn("nepaskelbtas", text)

    def test_register_page_shows_published_names_and_numbers(self):
        doc = apply_candidate_lists(register(), published_overlay(register()))
        validate_register(doc)
        text = register_content(dataset(), doc, checks())
        self.assertIn("Kandidatas 1A", text)
        self.assertIn("Kandidatas 1B", text)
        self.assertIn("<b>1</b>", text)
        self.assertNotIn("nepaskelbtos", text)

    def test_register_page_keeps_campaign_participants_distinct_after_publication(self):
        doc = apply_candidate_lists(register(), published_overlay(register()))
        text = register_content(dataset(), doc, checks())
        self.assertIn("registruotas kandidatas", text)
        self.assertNotIn("mero kampanijos dalyvis, ne rinkimų kandidatas", text)


class DynamicSnapshotTest(unittest.TestCase):
    def test_rendered_pages_follow_the_snapshot_without_hardcoded_october_dates(self):
        doc = deepcopy(register())
        doc["generated_at"] = "2027-01-15"
        doc["snapshot"]["date"] = "2027-01-15"
        doc["snapshot"]["index_retrieved_at"] = "2027-01-15T06:00:00+00:00"
        validate_register(doc)
        register_page = register_content(dataset(), doc, checks())
        index_page = index_content(dataset(), doc, {"sources": []}, checks())
        for page in (register_page, index_page):
            self.assertIn("2027-01-15", page)
            self.assertNotIn("2026-10-26", page)

    def test_rendered_pages_follow_a_variable_list_count(self):
        doc = add_new_list(register())
        index_page = index_content(dataset(), doc, {"sources": []}, checks())
        self.assertIn(f"{doc['vilnius']['list_participants_count']} sąrašų", index_page)
        self.assertIn("Naujoji partija", index_page)


if __name__ == "__main__":
    unittest.main()
