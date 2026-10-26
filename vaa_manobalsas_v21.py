#!/usr/bin/env python3
"""Record what Mano Balsas actually offers, and keep it out of the 2027 score.

The live Mano Balsas site (checked 2026-10-26) exposes 2024 national tests and
methodology.  It does not expose a 2027 Vilnius municipal test or municipal
party positions.  This script stores the methodology and test structure as
context-only.  It never writes a party stance into the 2027 dataset.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from vaa_fetch_v21 import ROOT, fetch, normalise, quote_present, sha256_text

OUT = ROOT / "vaa" / "manobalsas_2024_context.json"
FORMAT = "vaa-manobalsas-context-v1"
METHOD_URL = "https://www.manobalsas.lt/metodika/metodika.php"
TEST_URL = "https://www.manobalsas.lt/testas/testas.php?tst=43"

METHOD_QUOTES = [
    {
        "id": "positions_from_questionnaire",
        "quote": "Partijų ir kandidatų pozicijos buvo nustatomos pagal jų pateiktus atsakymus į testo klausimyną.",
    },
    {
        "id": "four_dimensions",
        "quote": "Vertinama pozicija atskirai kiekvienoje iš keturių politinių alternatyvų dimensijų: rinka ir valstybė, visuomenė, ekologija, užsienio ir gynybos politika.",
    },
    {
        "id": "scores_minus_two_to_two",
        "quote": "priskiriamas balas nuo -2 iki 2",
    },
    {
        "id": "unknown_excluded",
        "quote": "Jei partija neturi nuomonės, atitinkamas klausimas negauna balo ir į skaičiavimus neįtraukiamas.",
    },
    {
        "id": "graph_coordinates",
        "quote": "Partijų ir rinkėjo pozicijos (konkrečiose dimensijose) vaizduojamos grafike – dvimatėje koordinačių plokštumoje.",
    },
]


class ContextError(ValueError):
    pass


def _source_row(got: dict, source_id: str, publisher: str, published: str, label: str) -> dict:
    return {
        "source_id": source_id,
        "url": got["url"],
        "final_url": got.get("final_url"),
        "http_status": got.get("http_status"),
        "content_sha256": got.get("content_sha256"),
        "text_sha256": got.get("text_sha256"),
        "published": published,
        "publisher": publisher,
        "label": label,
        "retrieved_at": got.get("retrieved_at"),
    }


def build() -> dict:
    method = fetch(METHOD_URL)
    test = fetch(TEST_URL)
    for name, got in (("methodology", method), ("test", test)):
        if got.get("http_status") != 200 or not got.get("text"):
            raise ContextError(f"Mano Balsas {name} unavailable: HTTP {got.get('http_status')}")

    for item in METHOD_QUOTES:
        if not quote_present(method["text"], item["quote"]):
            raise ContextError("methodology quote missing: " + item["id"])

    test_text = test["text"]
    dimension_headings = [
        "Rinka ir valstybė",
        "Visuomenė",
        "Ekologija",
        "Užsienio ir gynybos politika",
        "Kiti klausimai",
    ]
    for heading in dimension_headings:
        if f"Seimo rinkimai 2024: {heading}" not in test_text:
            raise ContextError("test dimension heading missing: " + heading)

    raw = test.get("raw", b"").decode("utf-8", "replace")
    question_count = len(re.findall(r'name="a\d+"', raw))
    if question_count < 30:
        raise ContextError(f"unexpected Mano Balsas question count: {question_count}")

    doc = {
        "format": FORMAT,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "context_only",
        "scope_note": (
            "2024 m. Seimo rinkimų nacionalinė metodika ir klausimynas. "
            "Tai nėra 2027 m. Vilniaus savivaldybės rinkimų testas, "
            "ir šios nacionalinės pozicijos nenaudojamos 2027 m. vietos sąrašų balui."
        ),
        "methodology": {
            "title": "Metodika (Seimo rinkimai 2024)",
            "url": METHOD_URL,
            "quotes": [
                {
                    **item,
                    "quote_sha256": sha256_text(normalise(item["quote"])),
                    "source_id": "manobalsas_methodology",
                }
                for item in METHOD_QUOTES
            ],
            "dimensions": dimension_headings[:4],
            "scoring": (
                "Atsakymai koduojami nuo -2 iki 2; partijos be nuomonės negauna balo ir "
                "į dimensijos skaičiavimą neįtraukiamos. Dimensijų pozicijos vaizduojamos grafike."
            ),
        },
        "test": {
            "title": "Seimo rinkimai 2024",
            "url": TEST_URL,
            "question_count": question_count,
            "dimensions": dimension_headings,
            "format": "2024 national parliamentary test; 45 statements across five sections",
        },
        "quotes": [
            {
                **item,
                "source_id": "manobalsas_methodology",
                "source_url": METHOD_URL,
            }
            for item in METHOD_QUOTES
        ],
        "use_in_v21": {
            "used_for": [
                "questionnaire structure",
                "explicit unknown/no-opinion handling",
                "separate dimension reporting",
                "per-question agreement instead of a single opaque ranking",
            ],
            "not_used_for": [
                "2027 Vilnius party positions",
                "2027 municipal candidate recommendations",
                "national 2024 coordinates as local promises",
            ],
        },
        "sources": [
            _source_row(
                method,
                "manobalsas_methodology",
                "Mano Balsas",
                "2024-01-01",
                "Mano Balsas 2024 m. Seimo rinkimų metodika",
            ),
            _source_row(
                test,
                "manobalsas_test",
                "Mano Balsas",
                "2024-01-01",
                "Mano Balsas 2024 m. Seimo rinkimų testas",
            ),
        ],
        "limits": [
            "Mano Balsas 2026-10-26 neturėjo 2027 m. Vilniaus savivaldybės testo.",
            "2024 m. nacionalinės partijų pozicijos negali būti perkeltos į 2027 m. Vilniaus sąrašus.",
            "Metodika naudojama kaip dizaino įkvėpimas, ne kaip partijų vietos pažadų šaltinis.",
        ],
    }
    validate(doc)
    return doc


def validate(doc: dict) -> None:
    if doc.get("format") != FORMAT:
        raise ContextError("format invalid")
    if doc.get("status") != "context_only":
        raise ContextError("status must be context_only")
    if len(doc.get("methodology", {}).get("quotes", [])) != len(METHOD_QUOTES):
        raise ContextError("methodology quotes incomplete")
    if doc.get("test", {}).get("question_count", 0) < 30:
        raise ContextError("question count invalid")
    if len(doc.get("sources", [])) != 2:
        raise ContextError("two Mano Balsas sources required")
    for row in doc["sources"]:
        if row.get("http_status") != 200:
            raise ContextError(f"{row.get('source_id')}: HTTP failure")
        if not re.fullmatch(r"[0-9a-f]{64}", row.get("content_sha256") or ""):
            raise ContextError(f"{row.get('source_id')}: content hash missing")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="fetch and rewrite the context file")
    parser.add_argument("--check", action="store_true", help="validate the checked-in context file")
    args = parser.parse_args()
    if args.refresh:
        doc = build()
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("wrote", OUT.relative_to(ROOT))
    if args.check:
        doc = json.loads(OUT.read_text("utf-8"))
        validate(doc)
        print(
            "OK: Mano Balsas context only, {n} questions, {d} dimensions".format(
                n=doc["test"]["question_count"], d=len(doc["test"]["dimensions"])
            )
        )
    if not (args.refresh or args.check):
        parser.print_help()


if __name__ == "__main__":
    main()
