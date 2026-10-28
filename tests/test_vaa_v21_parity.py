"""Parity: vaa_v21.py and vaa_engine_v21.js must agree on every valid input."""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from vaa_v21 import VaaError, load_data, score_v21  # noqa: E402

ENGINE = ROOT / "vaa_engine_v21.js"
QUIZ = ROOT / "quiz_v21.html"


def cases(doc: dict) -> list[dict]:
    statements = {s["id"]: s for s in doc["statements"]}
    topics = [t["id"] for t in doc["topics"]]
    all_2 = {sid: 2 for sid in statements}
    all_0 = {sid: 0 for sid in statements}
    all_minus_2 = {sid: -2 for sid in statements}
    mixed = {
        "rail_transit": 2,
        "a_lanes": 1,
        "bike_network": 0,
        "transit_affordability": -1,
        "housing_land_supply": 2,
        "civil_shelters": 1,
        "resilience_centers": -2,
    }
    sparse = {
        "kindergarten_places": 1,
        "all_day_school": -1,
        "civil_shelters": 2,
    }
    return [
        {"selected_topics": topics, "top_topic": "transport", "answers": all_2},
        {"selected_topics": ["transport", "housing", "safety"], "top_topic": "housing", "answers": mixed},
        {"selected_topics": ["education", "safety"], "top_topic": "safety", "answers": sparse},
        {"selected_topics": ["transport"], "top_topic": "transport", "answers": {"rail_transit": 2}},
        {"selected_topics": ["transport"], "top_topic": "transport", "answers": {"rail_transit": 2, "a_lanes": 2}},
        {"selected_topics": topics, "top_topic": "transport", "answers": {}},
        {"selected_topics": topics, "top_topic": "transport", "answers": all_0},
        {"selected_topics": topics, "top_topic": "transport", "answers": all_minus_2},
        {"selected_topics": ["transport", "education", "safety"], "top_topic": "safety", "answers": sparse},
        {"selected_topics": ["transport"], "top_topic": "education", "answers": {"rail_transit": 2, "a_lanes": 2}},
        {"selected_topics": [], "answers": {}},
        {
            "selected_topics": ["transport", "education", "safety"],
            "top_topic": "transport",
            "answers": {
                "rail_transit": 1,
                "kindergarten_places": 2,
                "all_day_school": -1,
                "pedestrian_safety": 0,
                "civil_shelters": -2,
                "resilience_centers": 1,
            },
        },
    ]


RUNNER = r"""
const fs = require('fs');
const { scoreV21 } = require(process.argv[2]);
const data = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const cases = JSON.parse(fs.readFileSync(0, 'utf8'));
function sort(value) {
  if (Array.isArray(value)) return value.map(sort);
  if (value && typeof value === 'object') {
    const out = {};
    for (const key of Object.keys(value).sort()) out[key] = sort(value[key]);
    return out;
  }
  return value;
}
const output = cases.map((selection) => {
  try {
    return { ok: sort(scoreV21(data, selection)) };
  } catch (error) {
    return { error: true };
  }
});
process.stdout.write(JSON.stringify(output));
"""


def run_python(doc: dict, selection: dict) -> dict:
    try:
        return {"ok": score_v21(doc, selection)}
    except VaaError:
        return {"error": True}


@unittest.skipUnless(shutil.which("node"), "node not installed")
class EngineParityTest(unittest.TestCase):
    def setUp(self):
        self.doc = load_data()
        self.cases = cases(self.doc)

    def run_js(self, cases_to_run: list[dict]) -> list[dict]:
        with tempfile.TemporaryDirectory() as directory:
            runner = Path(directory) / "run.js"
            runner.write_text(RUNNER, "utf-8")
            completed = subprocess.run(
                [
                    "node",
                    str(runner),
                    str(ENGINE),
                    str(ROOT / "vaa" / "vilnius_2027_v21.json"),
                ],
                input=json.dumps(cases_to_run, ensure_ascii=False),
                capture_output=True,
                text=True,
                timeout=60,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def test_valid_inputs_match_exactly(self):
        js = self.run_js(self.cases)
        self.assertEqual(len(js), len(self.cases))
        for index, (selection, got) in enumerate(zip(self.cases, js)):
            expected = json.loads(json.dumps(run_python(self.doc, selection)))
            with self.subTest(case=index):
                self.assertEqual(got, expected)

    def test_invalid_inputs_fail_on_both_sides(self):
        invalid = [
            {"selected_topics": "transport", "answers": {}},
            {"selected_topics": ["transport"], "answers": []},
            {"selected_topics": ["transport"], "answers": {"rail_transit": 3}},
            {"selected_topics": ["transport"], "answers": {"rail_transit": -3}},
            {"selected_topics": ["transport"], "answers": {"rail_transit": 1.5}},
            {"selected_topics": ["transport"], "answers": {"rail_transit": True}},
            {"selected_topics": ["transport"], "answers": {"rail_transit": "2"}},
        ]
        js = self.run_js(invalid)
        for index, selection in enumerate(invalid):
            with self.subTest(case=index):
                self.assertTrue(js[index].get("error"), js[index])
                self.assertEqual(run_python(self.doc, selection), {"error": True})

    def test_embedded_page_data_and_engine_match_source_files(self):
        from vaa_build_v21 import sync_party_mayors

        html = QUIZ.read_text("utf-8")
        embedded = re.search(r'<script id="app-data" type="application/json">(.*?)</script>', html, re.S)
        self.assertIsNotNone(embedded)
        raw = json.loads((ROOT / "vaa" / "vilnius_2027_v21.json").read_text("utf-8"))
        register = json.loads((ROOT / "vaa" / "vilnius_2027_register.json").read_text("utf-8"))
        self.assertEqual(json.loads(embedded.group(1)), sync_party_mayors(raw, register))
        self.assertIn(ENGINE.read_text("utf-8"), html)

    def test_fresh_quiz_build_matches_checked_in_page(self):
        from vaa_build_v21 import build_quiz, load_all

        data, register, _sources, manifest, checks, mb, _law = load_all()
        self.assertEqual(build_quiz(data, register, manifest, checks, mb), QUIZ.read_text("utf-8"))


if __name__ == "__main__":
    unittest.main()
