#!/usr/bin/env python3
"""Official VRK 2027 campaign register for Vilnius city (apygarda Nr. 57).

The register is deliberately a *participant* register, not a candidate ballot.
VRK has not published final candidate lists or list numbers yet.  The importer
records that absence instead of inventing candidates or ballot numbers.

Usage:
  python3 vaa_register_v21.py --refresh
  python3 vaa_register_v21.py --check
  python3 vaa_register_v21.py --print
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from pathlib import Path
from typing import Any

from vaa_fetch_v21 import CACHE, ROOT, fetch, normalise, quote_present, sha256_text

FORMAT = "vaa-vrk-2027-register-v1"
INDEX_URL = (
    "https://www.vrk.lt/statiniai/puslapiai/politKamp/1884/dalyviai/"
    "dalyviaiPagalPartijas.html"
)
STATIC_BASE = "https://www.vrk.lt/statiniai/puslapiai"
CALENDAR_URL = "https://vrk.lt/fs/prd/p/2026-10/Rinkimu%20grafikas%202027.pdf"
GENERAL_URL = (
    "https://www.vrk.lt/lt/2027-m-kovo-7-d-savivaldybiu-tarybu-nariu-ir-meru-"
    "rinkimai/bendra-informacija"
)
PROGRAMMES_URL = (
    "https://www.vrk.lt/lt/2027-m-kovo-7-d-savivaldybiu-tarybu-nariu-ir-meru-"
    "rinkimai/kandidatu-rinkimu-programu-leidiniai"
)
BALLOT_URL = (
    "https://www.vrk.lt/lt/2027-m-kovo-7-d-savivaldybiu-tarybu-nariu-ir-meru-"
    "rinkimai/biuleteniu-pavyzdziai-ir-kiti-leidiniai"
)
PARTICIPANTS_URL = (
    "https://www.vrk.lt/lt/2027-m-kovo-7-d-savivaldybiu-tarybu-nariu-ir-meru-"
    "rinkimai/kampanijos-dalyviai-ir-suinteresuotieji"
)
OUT = ROOT / "vaa" / "vilnius_2027_register.json"
MANIFEST = ROOT / "vaa" / "review" / "vrk_2027_manifest.json"
FIXTURE = ROOT / "fixtures" / "vrk_2027_dalyviai.html"

PARTY_IDS = (
    ("Tėvynės sąjunga", "tslkd"),
    ("Laisvės partija", "lp"),
    ("Liberalų sąjūdis", "ls"),
    ("Lietuvos socialdemokratų partija", "lsdp"),
)
VILNIUS_RE = re.compile(r"Vilniaus miesto rinkimų apygardoje Nr\.\s*57", re.I)


class RegisterError(ValueError):
    pass


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _parent_blocks(source: str) -> list[dict[str, Any]]:
    pattern = re.compile(
        r"<b>(\d+)\.\s*(.*?)</b><br/>\s*(Partija|Politinis komitetas[^<]*)",
        re.S,
    )
    matches = list(pattern.finditer(source))
    blocks = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        chunk = source[match.end():end]
        blocks.append(
            {
                "number": int(match.group(1)),
                "name": _clean(re.sub(r"<[^>]+>", "", match.group(2))),
                "kind": _clean(re.sub(r"<[^>]+>", "", match.group(3))).rstrip(","),
                "chunk": chunk,
            }
        )
    return blocks


def _party_id(name: str) -> str | None:
    lowered = name.casefold()
    for needle, party_id in PARTY_IDS:
        if needle.casefold() in lowered:
            return party_id
    return None


def _entry_href(raw_href: str) -> str:
    match = re.search(r"srcUrl=([^&'\"]+)", raw_href)
    if not match:
        raise RegisterError("participant link has no srcUrl")
    path = match.group(1)
    if not path.startswith("/"):
        raise RegisterError("participant link is not absolute")
    return STATIC_BASE + path


def _entry_from_anchor(anchor_text: str, href: str) -> dict[str, Any] | None:
    text = _clean(re.sub(r"<[^>]+>", "", anchor_text))
    if not VILNIUS_RE.search(text):
        return None
    status = None
    code = re.search(r"\((S|A)\)\s*$", text)
    if code:
        status = code.group(1)
    text = re.sub(r"\s*\((?:S|A)\)\s*$", "", text).strip()
    text = re.sub(r"^\d+\.\s*", "", text)
    lowered = text.casefold()
    if "kandidatų sąrašas" in lowered:
        kind = "list"
        name = re.sub(r"\s*kandidatų sąrašas\s*$", "", text, flags=re.I).strip()
    elif re.search(r"kandidat(?:as|ė)\s+", text, re.I):
        kind = "mayor"
        name = re.split(r"kandidat(?:as|ė)\s+", text, maxsplit=1, flags=re.I)[1].strip()
    else:
        kind = "other"
        name = text
    return {
        "kind": kind,
        "name": name,
        "label": text,
        "status_code": status,
        "detail_url": _entry_href(href),
    }


def parse_index(source: str) -> dict[str, Any]:
    blocks = _parent_blocks(source)
    all_entries = []
    for block in blocks:
        party_id = _party_id(block["name"])
        anchors = re.findall(r"<a href='([^']+)'>\s*(.*?)</a>", block["chunk"], re.S)
        for href, anchor in anchors:
            entry = _entry_from_anchor(anchor, href)
            if entry is None:
                continue
            entry.update(
                participant_number=block["number"],
                participant_name=block["name"],
                participant_kind=block["kind"],
                party_id=party_id,
            )
            all_entries.append(entry)
    lists = [entry for entry in all_entries if entry["kind"] == "list"]
    mayors = [entry for entry in all_entries if entry["kind"] == "mayor"]
    by_party: dict[str, dict[str, Any]] = {}
    for entry in lists:
        pid = entry["party_id"]
        if pid is None:
            continue
        by_party.setdefault(pid, {"party_id": pid, "party_name": entry["participant_name"]})
        by_party[pid]["list"] = entry
    for entry in mayors:
        pid = entry["party_id"]
        if pid is None:
            continue
        by_party.setdefault(pid, {"party_id": pid, "party_name": entry["participant_name"]})
        by_party[pid]["mayor"] = entry
    return {
        "participants_count": len(blocks),
        "participants": [
            {"number": block["number"], "name": block["name"], "kind": block["kind"]}
            for block in blocks
        ],
        "vilnius_entries": all_entries,
        "vilnius_lists": list(by_party.values()),
    }


def parse_detail(source: str) -> dict[str, Any]:
    text = normalise(re.sub(r"<[^>]+>", " ", source))
    title_match = re.search(r"<h3>(.*?)</h3>", source, re.S)
    title = _clean(re.sub(r"<[^>]+>", "", title_match.group(1))) if title_match else None
    status_match = re.search(r"Statusas:\s*</td>\s*<td>(.*?)</td>", source, re.S)
    status = _clean(re.sub(r"<[^>]+>", "", status_match.group(1))) if status_match else None
    reg = re.search(
        r"Registruotas dalyviu\s+(\d{4}-\d{2}-\d{2})\s+sprendimu Nr\.\s*"
        r"(?:<[^>]+>\s*)*([A-Z0-9-]+)",
        source,
        re.S,
    )
    registered_on = reg.group(1) if reg else None
    decision = reg.group(2) if reg else None
    return {
        "detail_title": title,
        "status": status,
        "registered_on": registered_on,
        "decision": decision,
        "detail_text_sha256": sha256_text(text),
    }


def parse_calendar(text: str) -> dict[str, Any]:
    """Read only dates whose adjacent calendar line names the required step."""
    norm = normalise(text)
    # The PDF table inserts row numbers and day counts between the date cell
    # and the adjacent prose.  Require the date and both halves of each named
    # step, not one artificially contiguous string.
    checks = {
        "participant_registration_ends": (
            "2026 10 28",
            ["Baigiamas savarankiškų politinės kampanijos dalyvių registravimas."],
        ),
        "candidate_nomination_ends": (
            "2026 12 17",
            [
                "Baigiamas politinių komitetų ir kandidatų pareiškinių dokumentų pateikimas",
                "savivaldybės rinkimų komisijoje.",
            ],
        ),
        "candidate_lists_decision_by": (
            "2027 02 04",
            [
                "Paskutinė diena, kai VRK priima sprendimą registruoti kandidatą",
                "kandidatų sąrašą arba jų neregistruoti.",
            ],
        ),
        "candidate_lists_published_by": (
            "2027 02 05",
            [
                "VRK savo interneto svetainėje paskelbia kandidatų sąrašus, kandidatus, jų",
                "kandidatų sąrašų rinkimų numerius",
            ],
        ),
    }
    folded = norm.casefold()
    out = {}
    for key, (date, phrases) in checks.items():
        if date not in norm:
            raise RegisterError(f"calendar date missing for {key}")
        for phrase in phrases:
            if phrase.casefold() not in folded:
                raise RegisterError(f"calendar evidence missing for {key}: {phrase}")
        out[key] = {
            "date": date.replace(" ", "-"),
            "evidence": " … ".join(phrases),
            "source_url": CALENDAR_URL,
        }
    return out


def _manifest_row(row: dict[str, Any], source_id: str, publisher: str, published: str, label: str) -> dict[str, Any]:
    clean = {k: v for k, v in row.items() if k not in ("text", "raw")}
    clean.update(source_id=source_id, publisher=publisher, published=published, label=label)
    return clean


def refresh() -> dict[str, Any]:
    index = fetch(INDEX_URL)
    if index["http_status"] != 200:
        raise RegisterError(f"VRK index unavailable: HTTP {index['http_status']}")
    index_html = index["raw"].decode("utf-8", "replace")
    parsed = parse_index(index_html)

    rows = [
        _manifest_row(
            index,
            "vrk_participants",
            "Vyriausioji rinkimų komisija",
            "2026-10-26",
            "2027 m. kampanijos dalyvių sąrašas pagal partijas",
        )
    ]

    for group in parsed["vilnius_lists"]:
        for key in ("list", "mayor"):
            entry = group.get(key)
            if not entry:
                continue
            detail = fetch(entry["detail_url"])
            rows.append(
                _manifest_row(
                    detail,
                    f"vrk_detail_{entry['detail_url'].rsplit('pkdId-', 1)[-1].split('.')[0]}",
                    "Vyriausioji rinkimų komisija",
                    "2026-10-26",
                    entry["label"],
                )
            )
            if detail["http_status"] == 200:
                entry.update(parse_detail(detail["raw"].decode("utf-8", "replace")))
            else:
                entry.update(
                    detail_title=None,
                    status=None,
                    registered_on=None,
                    decision=None,
                    detail_text_sha256=None,
                )

    calendar = fetch(CALENDAR_URL)
    if calendar["http_status"] != 200:
        raise RegisterError(f"VRK calendar unavailable: HTTP {calendar['http_status']}")
    rows.append(
        _manifest_row(
            calendar,
            "vrk_calendar",
            "Vyriausioji rinkimų komisija",
            "2026-10-26",
            "2027 m. kovo 7 d. rinkimų grafikas",
        )
    )
    official_status = parse_calendar(calendar["text"])

    programmes = fetch(PROGRAMMES_URL)
    if programmes["http_status"] != 200:
        raise RegisterError(f"VRK programme page unavailable: HTTP {programmes['http_status']}")
    rows.append(
        _manifest_row(
            programmes,
            "vrk_programmes",
            "Vyriausioji rinkimų komisija",
            "2026-10-26",
            "Kandidatų rinkimų programų leidiniai",
        )
    )
    programmes_pending = quote_present(programmes["text"], "Informacija ruošiama")
    if not programmes_pending:
        raise RegisterError("VRK programme page no longer contains 'Informacija ruošiama'; review status manually")

    ballot_page = fetch(BALLOT_URL)
    if ballot_page["http_status"] != 200:
        raise RegisterError(f"VRK ballot examples page unavailable: HTTP {ballot_page['http_status']}")
    rows.append(
        _manifest_row(
            ballot_page,
            "vrk_ballot_examples",
            "Vyriausioji rinkimų komisija",
            "2026-10-26",
            "Biuletenių pavyzdžiai ir kiti leidiniai",
        )
    )
    ballot_pending = quote_present(ballot_page["text"], "Informacija ruošiama")
    if not ballot_pending:
        raise RegisterError("VRK ballot page no longer contains 'Informacija ruošiama'; review status manually")

    general = fetch(GENERAL_URL)
    if general["http_status"] == 200:
        rows.append(
            _manifest_row(
                general,
                "vrk_general",
                "Vyriausioji rinkimų komisija",
                "2026-10-07",
                "Bendra informacija apie 2027 m. rinkimus",
            )
        )

    participants = fetch(PARTICIPANTS_URL)
    if participants["http_status"] == 200:
        rows.append(
            _manifest_row(
                participants,
                "vrk_participants_page",
                "Vyriausioji rinkimų komisija",
                "2026-10-26",
                "Kampanijos dalyviai ir suinteresuotieji",
            )
        )

    lists = []
    for group in parsed["vilnius_lists"]:
        list_entry = group.get("list")
        if not list_entry:
            continue
        mayor = group.get("mayor")
        lists.append(
            {
                "party_id": group["party_id"],
                "party_name": group["party_name"],
                "list_name": list_entry["label"],
                "participant_number": list_entry["participant_number"],
                "registered_on": list_entry.get("registered_on"),
                "decision": list_entry.get("decision"),
                "status": list_entry.get("status"),
                "detail_url": list_entry["detail_url"],
                "mayor_candidate": (
                    {
                        "name": mayor["name"],
                        "registered_on": mayor.get("registered_on"),
                        "decision": mayor.get("decision"),
                        "status": mayor.get("status"),
                        "detail_url": mayor["detail_url"],
                    }
                    if mayor
                    else None
                ),
                "candidate_names_published": False,
                "ballot_number": None,
            }
        )

    doc = {
        "format": FORMAT,
        "generated_at": "2026-10-26",
        "election": {
            "id": "2027-municipal",
            "date": "2027-03-07",
            "municipality": "Vilnius",
            "municipality_code": "Vilniaus miesto savivaldybė",
            "apygarda": 57,
            "council_seats": 51,
            "official_page": GENERAL_URL,
        },
        "official_status": {
            **official_status,
            "candidate_lists_published": False,
            "candidate_names_published": False,
            "ballot_numbers_published": False,
            "programme_publications_published": False,
            "programme_publications_status": "pending",
            "programme_publications_note": "VRK puslapyje nurodyta „Informacija ruošiama“; dokumentų dar nėra.",
            "programme_publications_url": PROGRAMMES_URL,
            "ballot_examples_published": False,
            "ballot_examples_status": "pending",
            "ballot_examples_note": "VRK puslapyje nurodyta „Informacija ruošiama“; biuletenių pavyzdžių dar nėra.",
            "ballot_examples_url": BALLOT_URL,
            "status_note": (
                "VRK kandidatų rinkimų programų ir biuletenių pavyzdžių puslapiuose nurodyta „Informacija ruošiama“. "
                "Tai reiškia, kad dokumentų dar nėra; tai nėra teigiamas publikacijos patvirtinimas. "
                "Registre matomi kampanijos dalyviai, o ne galutiniai kandidatų sąrašai ar jų numeriai."
            ),
            "source_urls": [GENERAL_URL, PROGRAMMES_URL, BALLOT_URL, PARTICIPANTS_URL, CALENDAR_URL],
        },
        "national": {
            "participants_count": parsed["participants_count"],
            "participants": parsed["participants"],
            "source_url": INDEX_URL,
        },
        "vilnius": {
            "apygarda": 57,
            "registered_lists_count": len(lists),
            "registered_mayor_candidates_count": sum(1 for row in lists if row["mayor_candidate"]),
            "lists": lists,
        },
        "source": {
            "index_url": INDEX_URL,
            "fixture": "fixtures/vrk_2027_dalyviai.html",
            "manifest": "vaa/review/vrk_2027_manifest.json",
            "index_sha256": index["content_sha256"],
            "index_text_sha256": index["text_sha256"],
            "index_retrieved_at": index["retrieved_at"],
        },
        "limits": [
            "Tai kampanijos dalyvių registras, ne galutinis biuletenis.",
            "Galutiniai kandidatų sąrašai ir numeriai VRK svetainėje turi būti paskelbti iki 2027-02-05.",
            "Nė vienas kandidato vardas ar sąrašo numeris čia nenurodytas, nes VRK jų dar nepaskelbė.",
            "VRK biuletenių pavyzdžių puslapyje taip pat nurodyta „Informacija ruošiama“; balsavimo žymėjimo taisyklės dar nėra oficialiai paskelbtos.",
            "Registracija gali keistis iki 2026-10-28; šis failas yra 2026-10-26 būsena.",
        ],
    }
    validate_register(doc)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(
        json.dumps(
            {
                "format": "vaa-vrk-2027-manifest-v1",
                "generated_at": "2026-10-26",
                "sources": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    FIXTURE.parent.mkdir(parents=True, exist_ok=True)
    raw_path = CACHE / (index["content_sha256"] + ".raw")
    if raw_path.exists():
        FIXTURE.write_bytes(raw_path.read_bytes())
    return doc


def validate_register(doc: dict[str, Any]) -> None:
    if doc.get("format") != FORMAT:
        raise RegisterError("invalid format")
    if doc.get("election", {}).get("date") != "2027-03-07":
        raise RegisterError("election date changed")
    if doc.get("election", {}).get("apygarda") != 57:
        raise RegisterError("Vilnius apygarda must be 57")
    status = doc.get("official_status")
    if not isinstance(status, dict):
        raise RegisterError("official_status required")
    for key in ("participant_registration_ends", "candidate_nomination_ends",
                "candidate_lists_decision_by", "candidate_lists_published_by"):
        if not isinstance(status.get(key), dict) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", status[key].get("date", "")):
            raise RegisterError(f"missing official date: {key}")
    if status.get("candidate_lists_published") is not False:
        raise RegisterError("this register must not claim final candidate lists")
    if status.get("candidate_names_published") is not False:
        raise RegisterError("this register must not claim candidate names")
    if status.get("ballot_numbers_published") is not False:
        raise RegisterError("this register must not claim ballot numbers")
    if status.get("programme_publications_published") is not False:
        raise RegisterError("programme publications cannot be claimed as published")
    if status.get("programme_publications_status") != "pending":
        raise RegisterError("programme publications must be marked pending")
    if status.get("ballot_examples_published") is not False:
        raise RegisterError("ballot examples cannot be claimed as published")
    if status.get("ballot_examples_status") != "pending":
        raise RegisterError("ballot examples must be marked pending")
    lists = doc.get("vilnius", {}).get("lists")
    if not isinstance(lists, list) or not lists:
        raise RegisterError("Vilnius registered lists required")
    seen = set()
    for row in lists:
        pid = row.get("party_id")
        if not isinstance(pid, str) or not pid:
            raise RegisterError("party_id required for each registered list")
        if pid in seen:
            raise RegisterError("duplicate party list: " + pid)
        seen.add(pid)
        if not row.get("registered_on"):
            raise RegisterError(f"{pid}: registration date required")
        if row.get("ballot_number") is not None:
            raise RegisterError(f"{pid}: ballot number must remain null until VRK publishes it")
        if row.get("candidate_names_published") is not False:
            raise RegisterError(f"{pid}: candidate names must remain unpublished")
        if not str(row.get("detail_url", "")).startswith("https://www.vrk.lt/"):
            raise RegisterError(f"{pid}: official detail URL required")
    if seen != {"tslkd", "ls", "lp", "lsdp"}:
        raise RegisterError("expected the four currently registered Vilnius lists; found " + ",".join(sorted(seen)))
    source = doc.get("source", {})
    if not re.fullmatch(r"[0-9a-f]{64}", source.get("index_sha256", "")):
        raise RegisterError("index hash required")


def check() -> None:
    doc = json.loads(OUT.read_text("utf-8"))
    validate_register(doc)
    manifest = json.loads(MANIFEST.read_text("utf-8"))
    if manifest.get("format") != "vaa-vrk-2027-manifest-v1":
        raise RegisterError("manifest format invalid")
    by_id = {row.get("source_id"): row for row in manifest.get("sources", [])}
    for key in ("vrk_participants", "vrk_calendar", "vrk_programmes", "vrk_ballot_examples"):
        if key not in by_id:
            raise RegisterError("manifest missing " + key)
    index_row = by_id["vrk_participants"]
    if index_row.get("content_sha256") != doc["source"]["index_sha256"]:
        raise RegisterError("index hash does not match manifest")
    if index_row.get("http_status") != 200:
        raise RegisterError("index was not retrieved successfully")
    if doc["official_status"].get("candidate_lists_published") is not False:
        raise RegisterError("final lists cannot be claimed yet")
    print(
        "OK: {n} Vilnius lists, {m} registered mayor candidates, final lists expected by {d}".format(
            n=doc["vilnius"]["registered_lists_count"],
            m=doc["vilnius"]["registered_mayor_candidates_count"],
            d=doc["official_status"]["candidate_lists_published_by"]["date"],
        )
    )


def print_summary() -> None:
    doc = json.loads(OUT.read_text("utf-8"))
    validate_register(doc)
    print("Vilnius 2027 official register —", doc["generated_at"])
    print("Election:", doc["election"]["date"], "| apygarda", doc["election"]["apygarda"])
    print("Final lists published:", doc["official_status"]["candidate_lists_published"])
    print("Expected by:", doc["official_status"]["candidate_lists_published_by"]["date"])
    for row in doc["vilnius"]["lists"]:
        mayor = row["mayor_candidate"]
        print(
            f"  {row['party_id']:5} registered {row['registered_on']}  "
            f"mayor: {mayor['name'] if mayor else '—'}  number: {row['ballot_number'] or '—'}"
        )
    print("National participants:", doc["national"]["participants_count"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="fetch VRK and rewrite the register")
    parser.add_argument("--check", action="store_true", help="validate the checked-in register")
    parser.add_argument("--print", dest="show", action="store_true", help="print a short summary")
    args = parser.parse_args()
    if args.refresh:
        doc = refresh()
        print("wrote", OUT.relative_to(ROOT), "with", doc["vilnius"]["registered_lists_count"], "lists")
    if args.check:
        check()
    if args.show:
        print_summary()
    if not (args.refresh or args.check or args.show):
        parser.print_help()


if __name__ == "__main__":
    main()
