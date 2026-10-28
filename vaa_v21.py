#!/usr/bin/env python3
"""Validation and scoring for the 2027 Vilnius civic quiz (v21).

The engine is intentionally small and deterministic. It scores only registered
Vilnius lists, only statements the user answered, and only evidence rows that
passed every gate: explicit stance, verbatim quote, current campaign source,
party attribution, and a successful quote check.

A high score is an evidence-agreement summary, not a voting instruction and not
a prediction. Missing evidence is never treated as disagreement.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from vaa_fetch_v21 import normalise, sha256_text

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "vaa" / "vilnius_2027_v21.json"
REGISTRY = ROOT / "vaa" / "sources_2027_v21.json"
CHECKS = ROOT / "vaa" / "review" / "source_checks_v21.json"
FORMAT = "lt-civic-vaa-2027-v1"
STANCE_VALUE = {"agree": 2, "lean_agree": 1, "neutral": 0, "lean_disagree": -1, "disagree": -2}
MIN_COVERAGE_PCT = 35
CLOSE_MARGIN = 5
MIN_RECOMMEND_SCORE = 50
PARTY_IDS = {"tslkd", "lp", "ls", "lsdp"}
TOPIC_IDS = {"transport", "education", "housing", "governance", "safety", "economy"}
UTILITY_STANCES = {"unknown"}
VALID_USER_VALUES = {-2, -1, 0, 1, 2}
GOVERNMENT_LEVELS = {"local", "national", "mixed", "unclear", "not_applicable"}
DOCUMENT_STATUSES = {"draft", "adopted", "campaign_statement", "not_found"}
SPEAKER_AUTHORITIES = {"party", "candidate", "officeholder", "council_group", "coalition", "not_applicable"}
INTERPRETATION_KEYS = (
    "supports_full_proposition",
    "conditions",
    "costs_tradeoffs",
    "government_level",
    "document_status",
    "speaker_authority",
    "human_approved",
    "reviewed_by",
    "limitations",
)


class VaaError(ValueError):
    pass


def round_div(num: int, den: int) -> int | None:
    if not den:
        return None
    return (num + den // 2) // den


def pct(part: int, whole: int) -> int:
    if not whole:
        return 0
    return (part * 100 + whole // 2) // whole


def confidence_for(coverage_pct: int, known: int) -> str:
    if coverage_pct >= 70 and known >= 4:
        return "high"
    if coverage_pct >= 50:
        return "medium"
    return "low"


def _sort_key(row: dict[str, Any]) -> tuple[int, int, str]:
    return (-row["score"], -row["coverage_pct"], row["party_id"])


def score_v21(data: dict[str, Any], selection: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(selection, dict):
        raise VaaError("selection must be an object")
    raw_selected = selection.get("selected_topics")
    if raw_selected is not None and not isinstance(raw_selected, list):
        raise VaaError("selected_topics must be a list")
    selected = list(raw_selected or [])
    answers = selection.get("answers")
    if answers is None:
        answers = {}
    if not isinstance(answers, dict):
        raise VaaError("answers must be an object")
    for statement_id, value in answers.items():
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value not in VALID_USER_VALUES:
            raise VaaError(f"{statement_id}: answer must be -2, -1, 0, 1, 2 or null")
    top_topic = selection.get("top_topic")
    if top_topic not in selected:
        top_topic = selected[0] if selected else None

    topic_by_id = {topic["id"]: topic for topic in data["topics"]}
    selected_statements = [s for s in data["statements"] if s["topic_id"] in selected]
    selected_ids = [s["id"] for s in selected_statements]
    answered_count = sum(1 for sid in selected_ids if answers.get(sid) is not None)
    minimum_answers = min(2, len(selected_statements))
    enough_answers = bool(selected) and answered_count >= minimum_answers

    party_rows = []
    for party in data["parties"]:
        known = answered = num = den = 0
        covered: set[str] = set()
        gap_answered: set[str] = set()
        topic_stats = {
            tid: {"score": None, "known": 0, "answered": 0, "coverage_pct": 0, "_num": 0, "_den": 0}
            for tid in selected
        }
        for statement in selected_statements:
            user = answers.get(statement["id"])
            if user is None:
                continue
            answered += 1
            stats = topic_stats[statement["topic_id"]]
            stats["answered"] += 1
            answer = data["answers"].get(party["id"], {}).get(statement["id"], {})
            stance = answer.get("stance")
            interpretation = answer.get("interpretation") or {}
            if not (
                answer.get("scoreable") is True
                and answer.get("quote_verified") is True
                and interpretation.get("supports_full_proposition") is True
                and stance in STANCE_VALUE
            ):
                gap_answered.add(statement["topic_id"])
                continue
            points = 100 - 25 * abs(int(user) - STANCE_VALUE[stance])
            weight = 2 if statement["topic_id"] == top_topic else 1
            num += points * weight
            den += weight
            known += 1
            covered.add(statement["topic_id"])
            stats["known"] += 1
            stats["_num"] += points * weight
            stats["_den"] += weight
        for tid, stats in topic_stats.items():
            stats["score"] = round_div(stats.pop("_num"), stats.pop("_den"))
            stats["coverage_pct"] = pct(stats["known"], stats["answered"])
        coverage_pct = pct(known, answered)
        topics_covered = [tid for tid in selected if tid in covered]
        gaps = [tid for tid in selected if tid in gap_answered and tid not in covered]
        min_known = min(3, answered)
        eligible = (
            enough_answers
            and den > 0
            and known >= min_known
            and coverage_pct >= MIN_COVERAGE_PCT
            and len(topics_covered) >= min(2, len(selected))
        )
        party_rows.append(
            {
                "party_id": party["id"],
                "short_name": party["short_name"],
                "name": party["name"],
                "color": party["color"],
                "mayor_candidate": party.get("mayor_candidate"),
                "score": round_div(num, den),
                "coverage_pct": coverage_pct,
                "known": known,
                "answered": answered,
                "eligible": eligible,
                "confidence": confidence_for(coverage_pct, known),
                "topics_covered": topics_covered,
                "gaps": gaps,
                "topic_scores": topic_stats,
                "rank": None,
            }
        )

    eligible_rows = sorted([p for p in party_rows if p["eligible"]], key=_sort_key)
    for index, row in enumerate(eligible_rows, start=1):
        row["rank"] = index
    ineligible = sorted([p for p in party_rows if not p["eligible"]], key=lambda p: p["party_id"])
    parties = eligible_rows + ineligible
    top = eligible_rows[0] if eligible_rows else None
    second = eligible_rows[1] if len(eligible_rows) > 1 else None
    shortlist = []
    if top:
        shortlist = [p for p in eligible_rows if p["score"] >= top["score"] - CLOSE_MARGIN][:3]
    if not top:
        status = "insufficient"
    elif top["score"] < MIN_RECOMMEND_SCORE:
        status = "no_match"
    elif len(shortlist) > 1 or top["coverage_pct"] < 50:
        status = "close"
    else:
        status = "clear"
    close_reason = None
    if status == "close":
        if len(shortlist) > 1 and top["coverage_pct"] < 50:
            close_reason = "margin_and_coverage"
        elif len(shortlist) > 1:
            close_reason = "margin"
        else:
            close_reason = "coverage"

    topic_results = []
    for tid in selected:
        topic = topic_by_id.get(tid, {"id": tid, "name": tid})
        rows = [
            {
                "party_id": p["party_id"],
                "short_name": p["short_name"],
                "color": p["color"],
                **{
                    "score": p["topic_scores"][tid]["score"],
                    "known": p["topic_scores"][tid]["known"],
                    "answered": p["topic_scores"][tid]["answered"],
                    "coverage_pct": p["topic_scores"][tid]["coverage_pct"],
                },
            }
            for p in party_rows
            if p["topic_scores"].get(tid, {}).get("known", 0) > 0
            and p["topic_scores"][tid].get("score") is not None
        ]
        rows.sort(key=lambda p: (-p["score"], -p["coverage_pct"], p["party_id"]))
        rows = rows[:3]
        topic_statement_count = sum(1 for s in selected_statements if s["topic_id"] == tid)
        best = rows[0] if rows else None
        topic_status = (
            "insufficient"
            if not best
            else (
                "thin"
                if best["known"] < min(2, topic_statement_count) or best["coverage_pct"] < 50
                else "clear"
            )
        )
        topic_results.append({"topic_id": tid, "name": topic["name"], "status": topic_status, "parties": rows})

    shortlist_ids = [p["party_id"] for p in shortlist]
    mayor_candidates = [
        {"party_id": p["party_id"], "short_name": p["short_name"], "name": p["mayor_candidate"]}
        for p in party_rows
        if p["mayor_candidate"]
    ]
    mayor = None
    if status == "clear" and top and top["mayor_candidate"]:
        mayor = {"party_id": top["party_id"], "short_name": top["short_name"], "name": top["mayor_candidate"]}
    final_lists_date = data.get("election", {}).get("final_lists_date", "2027-02-05")
    preference_votes = data.get("election", {}).get("preference_votes", 5)
    notes = [
        f"2027 m. galutiniai sąrašai ir numeriai VRK dar nepaskelbti (numatoma iki {final_lists_date}).",
        "Tai programų tekstų atitikimas, ne partijos, kandidato ar koalicijos rekomendacija.",
        "Rezultatas nevertina rinkimų kampanijos poveikio, galimų mandatų, koalicijų, kandidatų kompetencijos ar to, ar pažadas bus įgyvendintas.",
        "Nenaudok rezultato kaip strateginio balsavimo patarimo.",
    ]
    if status == "insufficient":
        notes.append("Nepakanka patikimų, cituojamų pozicijų, kad būtų pagrindas išskirti sąrašą.")
    elif status == "no_match":
        notes.append(
            "Geriausias turimas programų atitikimas nesiekia 50/100. Nepakanka pagrindo išskirti sąrašą; skaityk programas ir kandidatų sąrašus."
        )
    elif status == "close":
        if close_reason in ("margin", "margin_and_coverage"):
            notes.append(
                "Keli sąrašai skiriasi mažiau nei 5 balais. Žiūrėk į tau svarbiausią temą, o ne į vieną bendrą skaičių."
            )
        if close_reason in ("coverage", "margin_and_coverage"):
            notes.append("Viršutinio sąrašo aprėptis žema – trūksta pozicijų kai kuriais klausimais.")
        notes.append("Vieno sąrašo neišskiriame; žemiau pateikti artimiausi programų atitikimai.")
    if status == "clear" and top and not top["mayor_candidate"]:
        notes.append("Šis sąrašas neturėjo registruoto mero kampanijos dalyvio; mero biuletenyje rinkis atskirai.")
    if mayor_candidates:
        notes.append("Mero biuletenyje kol kas rodomi tik VRK registruoti mero kampanijos dalyviai; galutinis mero biuletenis dar nepaskelbtas.")
    notes.append(f"Pirmumo balsų taisyklę nustato Rinkimų kodekso 143 straipsnis (iki {preference_votes} pirmumo balsų; mažesniame sąraše – mažiau). Oficialų biuletenio išdėstymą patikrink VRK pavyzdyje.")
    notes.append("Prieš balsuodami patikrinkite oficialų VRK biuletenį.")

    party_by_id = {p["party_id"]: p for p in party_rows}
    topic_plan = [
        {"topic_id": row["topic_id"], "party_id": row["parties"][0]["party_id"], "score": row["parties"][0]["score"]}
        for row in topic_results
        if row["parties"]
    ]

    return {
        "status": status,
        "close_reason": close_reason,
        "confidence": top["confidence"] if top else "none",
        "selected_topics": selected,
        "top_topic": top_topic,
        "answered_count": answered_count,
        "minimum_answers": minimum_answers,
        "enough_answers": enough_answers,
        "parties": parties,
        "shortlist": shortlist_ids if status in ("close", "clear") else [],
        "top": top["party_id"] if top else None,
        "lead": (top["score"] - second["score"]) if top and second else None,
        "topic_results": topic_results,
        "plan": {
            "council_list": top["party_id"] if status == "clear" and top else None,
            "shortlist": shortlist_ids if status in ("close", "clear") else [],
            "mayor": mayor,
            "mayor_candidates": mayor_candidates,
            "preference_votes": preference_votes,
            "final_lists_date": final_lists_date,
            "ballot_number": None,
            "notes": notes,
            "topic_plan": topic_plan,
            "top_mayor": (party_by_id[top["party_id"]].get("mayor_candidate") if top else None),
        },
    }


def _validate_interpretation(party_id: str, statement_id: str, row: dict[str, Any], scoreable: bool) -> None:
    label = f"{party_id}/{statement_id}"
    value = row.get("interpretation")
    if not isinstance(value, dict):
        raise VaaError(f"{label}: interpretation object required")
    missing = [key for key in INTERPRETATION_KEYS if key not in value]
    if missing:
        raise VaaError(f"{label}: interpretation missing {', '.join(missing)}")
    supports = value.get("supports_full_proposition")
    if not isinstance(supports, bool):
        raise VaaError(f"{label}: supports_full_proposition must be boolean")
    if supports is not scoreable:
        raise VaaError(f"{label}: supports_full_proposition must match scoreable")
    if value.get("government_level") not in GOVERNMENT_LEVELS:
        raise VaaError(f"{label}: invalid government_level")
    if value.get("document_status") not in DOCUMENT_STATUSES:
        raise VaaError(f"{label}: invalid document_status")
    if value.get("speaker_authority") not in SPEAKER_AUTHORITIES:
        raise VaaError(f"{label}: invalid speaker_authority")
    if not isinstance(value.get("human_approved"), bool):
        raise VaaError(f"{label}: human_approved must be boolean")
    if value.get("human_approved") is True and row.get("review_status") != "human_approved":
        raise VaaError(f"{label}: human approval needs human_approved review_status")
    for key in ("conditions", "costs_tradeoffs", "reviewed_by", "limitations"):
        if not isinstance(value.get(key), str) or not value[key].strip():
            raise VaaError(f"{label}: interpretation {key} required")
    if scoreable:
        if value.get("document_status") == "not_found":
            raise VaaError(f"{label}: scoreable row cannot have not_found document status")
        if value.get("speaker_authority") == "not_applicable":
            raise VaaError(f"{label}: scoreable row needs a speaker authority")
    else:
        if supports is not False:
            raise VaaError(f"{label}: non-scoreable row must not claim full support")


def validate_data(data: dict[str, Any]) -> None:
    if data.get("format") != FORMAT:
        raise VaaError("dataset format invalid")
    if data.get("election", {}).get("date") != "2027-03-07":
        raise VaaError("election date must be 2027-03-07")
    topics = data.get("topics")
    statements = data.get("statements")
    parties = data.get("parties")
    answers = data.get("answers")
    if not isinstance(topics, list) or len(topics) != len(TOPIC_IDS):
        raise VaaError("expected six topics")
    topic_ids = {t.get("id") for t in topics}
    if topic_ids != TOPIC_IDS:
        raise VaaError("topic ids mismatch: " + ",".join(sorted(topic_ids)))
    if not isinstance(statements, list) or len(statements) != 13:
        raise VaaError("expected 13 statements")
    statement_ids = set()
    for statement in statements:
        sid = statement.get("id")
        if not sid or sid in statement_ids:
            raise VaaError("duplicate or missing statement id")
        statement_ids.add(sid)
        if statement.get("topic_id") not in topic_ids:
            raise VaaError(f"{sid}: unknown topic")
        if not statement.get("text") or not statement.get("context"):
            raise VaaError(f"{sid}: text and context required")
    if not isinstance(parties, list) or {p.get("id") for p in parties} != PARTY_IDS:
        raise VaaError("party ids mismatch")
    if not isinstance(answers, dict) or set(answers) != PARTY_IDS:
        raise VaaError("answers must cover exactly the four registered parties")
    for party in parties:
        for key in ("name", "short_name", "color", "registered_on", "list_2023", "share_2023_pct"):
            if party.get(key) in (None, ""):
                raise VaaError(f"{party.get('id')}: {key} required")
        if not str(party.get("registered_on")).startswith("2026-"):
            raise VaaError(f"{party.get('id')}: registered_on must be a 2026 registration date")
    for party_id, rows in answers.items():
        if set(rows) != statement_ids:
            missing = statement_ids - set(rows)
            extra = set(rows) - statement_ids
            raise VaaError(f"{party_id}: answer set mismatch missing={sorted(missing)} extra={sorted(extra)}")
        for statement_id, row in rows.items():
            if not isinstance(row, dict):
                raise VaaError(f"{party_id}/{statement_id}: object required")
            stance = row.get("stance")
            if stance not in STANCE_VALUE and stance not in UTILITY_STANCES:
                raise VaaError(f"{party_id}/{statement_id}: invalid stance {stance!r}")
            if stance in UTILITY_STANCES:
                if not row.get("note"):
                    raise VaaError(f"{party_id}/{statement_id}: unknown rows need a note")
                _validate_interpretation(party_id, statement_id, row, False)
                if row.get("interpretation", {}).get("document_status") != "not_found":
                    raise VaaError(f"{party_id}/{statement_id}: unknown row must use not_found document status")
                continue
            required = (
                "quote", "source_id", "source_url", "source_date", "source_type", "speaker",
                "election_year", "evidence_kind", "scope", "full_proposition", "party_attribution",
                "scoreable", "source_status", "quote_verified", "review_status", "review_note",
            )
            for key in required:
                if row.get(key) in (None, ""):
                    raise VaaError(f"{party_id}/{statement_id}: {key} required")
            if row.get("source_type") not in ("programme", "statement"):
                raise VaaError(f"{party_id}/{statement_id}: source_type must be programme or statement")
            if not str(row.get("source_url", "")).startswith("https://"):
                raise VaaError(f"{party_id}/{statement_id}: https source_url required")
            if row.get("election_year") != 2027:
                raise VaaError(f"{party_id}/{statement_id}: election_year must be 2027")
            if row.get("evidence_kind") != "verbatim":
                raise VaaError(f"{party_id}/{statement_id}: only verbatim evidence is accepted")
            if row.get("source_status") != "accessible":
                raise VaaError(f"{party_id}/{statement_id}: source_status must be accessible")
            if row.get("quote_verified") is not True:
                raise VaaError(f"{party_id}/{statement_id}: quote_verified must be true")
            if row.get("review_status") not in ("assistant_checked", "human_approved"):
                raise VaaError(f"{party_id}/{statement_id}: review_status not usable")
            if row.get("scoreable") is True:
                if row.get("full_proposition") is not True:
                    raise VaaError(f"{party_id}/{statement_id}: scoreable row must be a full proposition")
                if row.get("party_attribution") is not True:
                    raise VaaError(f"{party_id}/{statement_id}: scoreable row needs party attribution")
            else:
                if not row.get("review_note"):
                    raise VaaError(f"{party_id}/{statement_id}: non-scoreable row needs a reason")
                if row.get("full_proposition") is not False and row.get("party_attribution") is not False:
                    raise VaaError(
                        f"{party_id}/{statement_id}: non-scoreable row must identify a failed gate "
                        "(full_proposition or party_attribution)"
                    )
            _validate_interpretation(party_id, statement_id, row, row.get("scoreable") is True)
    evidence_review = data.get("evidence_review")
    if not isinstance(evidence_review, dict) or evidence_review.get("status") not in ("assistant_checked", "human_approved"):
        raise VaaError("evidence_review status required")
    if len(evidence_review.get("fields") or []) != len(INTERPRETATION_KEYS):
        raise VaaError("evidence_review fields must document every interpretation field")
    hv = data.get("human_validation")
    if not isinstance(hv, dict) or hv.get("status") not in ("pending", "approved"):
        raise VaaError("human_validation status required")
    if hv.get("status") == "approved":
        if not hv.get("approved_rows") or not hv.get("reviewers"):
            raise VaaError("approved human validation needs reviewer names and rows")
    if not data.get("limits") or not isinstance(data["limits"], list):
        raise VaaError("limits required")


def check_evidence(data: dict[str, Any], checks_doc: dict[str, Any] | None = None) -> None:
    if checks_doc is None:
        if not CHECKS.exists():
            raise VaaError("source checks missing; run vaa_sources_v21.py --refresh")
        checks_doc = json.loads(CHECKS.read_text("utf-8"))
    if checks_doc.get("format") != "vaa-source-checks-v21":
        raise VaaError("source checks format invalid")
    check_by_id = {row["evidence_id"]: row for row in checks_doc.get("checks", [])}
    registry = json.loads(REGISTRY.read_text("utf-8"))
    registry_by_id = {row["id"]: row for row in registry.get("sources", [])}
    checked = 0
    for party_id, rows in data["answers"].items():
        for statement_id, answer in rows.items():
            if not answer.get("quote"):
                continue
            evidence_id = f"{party_id}__{statement_id}"
            check = check_by_id.get(evidence_id)
            if not check:
                raise VaaError(f"{evidence_id}: no source check")
            if check.get("quote_found") is not True:
                raise VaaError(f"{evidence_id}: quote not found")
            if check.get("quote_sha256") != sha256_text(normalise(answer["quote"])):
                raise VaaError(f"{evidence_id}: quote changed since check")
            if check.get("source_id") != answer.get("source_id"):
                raise VaaError(f"{evidence_id}: source id mismatch")
            if check.get("source_url") != answer.get("source_url"):
                raise VaaError(f"{evidence_id}: source url mismatch")
            if check.get("source_http_status") != 200:
                raise VaaError(f"{evidence_id}: source was not HTTP 200")
            source = registry_by_id.get(answer.get("source_id"))
            if not source:
                raise VaaError(f"{evidence_id}: source missing from registry")
            if source.get("url") != answer.get("source_url"):
                raise VaaError(f"{evidence_id}: registry url mismatch")
            if source.get("published") != answer.get("source_date"):
                raise VaaError(f"{evidence_id}: source date mismatch with registry")
            if answer.get("scoreable") is True and check.get("scoreable") is not True:
                raise VaaError(f"{evidence_id}: scoreable row was checked as non-scoreable")
            checked += 1
    if checked < 20:
        raise VaaError("too few evidence checks")
    print(f"OK: {checked} evidence rows match source checks, registry and quote hashes")


def load_data() -> dict[str, Any]:
    data = json.loads(DATA.read_text("utf-8"))
    validate_data(data)
    return data


def demo() -> None:
    data = load_data()
    selected = [t["id"] for t in data["topics"]]
    answers = {s["id"]: 2 for s in data["statements"]}
    result = score_v21(data, {"selected_topics": selected, "answers": answers, "top_topic": "transport"})
    print(json.dumps(result, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate dataset and source checks")
    parser.add_argument("--demo", action="store_true", help="run the all-agree demo")
    args = parser.parse_args()
    try:
        if args.check:
            data = load_data()
            check_evidence(data)
            print(f"OK: {len(data['topics'])} topics, {len(data['statements'])} statements, {len(data['parties'])} parties")
        if args.demo:
            demo()
    except VaaError as error:
        print("ERROR:", error, file=sys.stderr)
        raise SystemExit(1)
    if not (args.check or args.demo):
        parser.print_help()


if __name__ == "__main__":
    main()
