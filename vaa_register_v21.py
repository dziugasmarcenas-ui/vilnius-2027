#!/usr/bin/env python3
"""Official VRK 2027 campaign register for Vilnius city (apygarda Nr. 57).

The register is deliberately a *participant* register, not a candidate ballot.
It discovers every Vilnius entry on the VRK page instead of assuming a fixed set
of parties, and it records the difference between four publication states:

  * campaign participant registration (this snapshot),
  * candidate-list registration (optional candidate_lists_2027.json overlay),
  * candidate names and ballot numbers (same overlay),
  * official programme publications and ballot examples (parsed from VRK pages).

Absent data stays absent.  The importer never invents a candidate, a ballot
number or a publication date.

Usage:
  python3 vaa_register_v21.py --refresh
  python3 vaa_register_v21.py --check
  python3 vaa_register_v21.py --print
"""
from __future__ import annotations

import argparse
import html
import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from vaa_fetch_v21 import CACHE, ROOT, fetch, normalise, quote_present, sha256_text

FORMAT = "vaa-vrk-2027-register-v2"
MANIFEST_FORMAT = "vaa-vrk-2027-manifest-v2"
OVERLAY_FORMAT = "vaa-candidate-lists-2027-v1"
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
OVERLAY = ROOT / "vaa" / "candidate_lists_2027.json"
MANIFEST = ROOT / "vaa" / "review" / "vrk_2027_manifest.json"
FIXTURE = ROOT / "fixtures" / "vrk_2027_dalyviai.html"

KNOWN_ORGANISATIONS = (
    ("Tėvynės sąjunga", "tslkd"),
    ("Laisvės partija", "lp"),
    ("Liberalų sąjūdis", "ls"),
    ("Lietuvos socialdemokratų partija", "lsdp"),
)
VILNIUS_RE = re.compile(r"Vilniaus miesto rinkimų apygardoje Nr\.\s*57", re.I)
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
DOCUMENT_SUFFIXES = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx")


class RegisterError(ValueError):
    pass


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def _slug(value: str) -> str:
    text = unicodedata.normalize("NFKD", value)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-zA-Z0-9]+", "_", text.casefold()).strip("_")
    return text[:40] or "unknown"


def _known_party_id(name: str) -> str | None:
    lowered = name.casefold()
    for needle, party_id in KNOWN_ORGANISATIONS:
        if needle.casefold() in lowered:
            return party_id
    return None


def _organisation_id(number: int, name: str) -> str:
    known = _known_party_id(name)
    if known:
        return known
    return f"org_{number:02d}_{_slug(name)}"


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
        party_id = _known_party_id(block["name"])
        organisation_id = _organisation_id(block["number"], block["name"])
        anchors = re.findall(r"<a href='([^']+)'>\s*(.*?)</a>", block["chunk"], re.S)
        for href, anchor in anchors:
            entry = _entry_from_anchor(anchor, href)
            if entry is None:
                continue
            entry.update(
                participant_number=block["number"],
                participant_name=block["name"],
                participant_kind=block["kind"],
                organisation_id=organisation_id,
                organisation_name=block["name"],
                organisation_kind=block["kind"],
                party_id=party_id,
            )
            all_entries.append(entry)
    lists = [entry for entry in all_entries if entry["kind"] == "list"]
    mayors = [entry for entry in all_entries if entry["kind"] == "mayor"]
    by_org: dict[str, dict[str, Any]] = {}
    for entry in lists:
        oid = entry["organisation_id"]
        by_org.setdefault(
            oid,
            {
                "organisation_id": oid,
                "party_id": entry["party_id"],
                "organisation_name": entry["organisation_name"],
                "organisation_kind": entry["organisation_kind"],
                "participant_number": entry["participant_number"],
            },
        )
        by_org[oid]["list"] = entry
    for entry in mayors:
        oid = entry["organisation_id"]
        by_org.setdefault(
            oid,
            {
                "organisation_id": oid,
                "party_id": entry["party_id"],
                "organisation_name": entry["organisation_name"],
                "organisation_kind": entry["organisation_kind"],
                "participant_number": entry["participant_number"],
            },
        )
        by_org[oid]["mayor"] = entry
    organisations = list(by_org.values())
    return {
        "participants_count": len(blocks),
        "participants": [
            {"number": block["number"], "name": block["name"], "kind": block["kind"]}
            for block in blocks
        ],
        "vilnius_entries": all_entries,
        "vilnius_lists": organisations,
        "organisations": organisations,
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


MAIN_CONTENT_RE = re.compile(
    r'<div class="main__container-content">(.*?)</main>', re.S | re.I
)


def _page_content(source: str) -> str | None:
    """Return only the page's own content region.

    VRK's global header and footer contain a permanent „Rinkimų grafikas“ PDF.
    Treating every anchor on the page as a publication therefore marks empty
    pages as published.  The content region is the only place where the page
    itself can publish programme or ballot documents.
    """
    match = MAIN_CONTENT_RE.search(source)
    return match.group(1) if match else None


def parse_publication_page(source: str) -> dict[str, Any]:
    """Classify a VRK publications page as pending, published or unknown.

    A page is *published* only when its own content region exposes at least one
    document link.  The words „Informacija ruošiama“ (HTML-escaped on the live
    page) mean the page exists but no documents have been published yet.
    """
    content = _page_content(source)
    documents: list[dict[str, str]] = []
    seen: set[str] = set()
    if content is not None:
        for href, anchor in re.findall(
            r"<a\s+href=['\"]([^'\"]+)['\"][^>]*>(.*?)</a>", content, re.S | re.I
        ):
            url = html.unescape(href).strip()
            title = _clean(re.sub(r"<[^>]+>", " ", anchor))
            lowered = url.casefold().split("?", 1)[0]
            if not lowered.endswith(DOCUMENT_SUFFIXES):
                continue
            if url.startswith("/"):
                url = "https://www.vrk.lt" + url
            if not url.startswith("https://"):
                continue
            if not title:
                title = url.rsplit("/", 1)[-1]
            if url in seen:
                continue
            seen.add(url)
            documents.append({"title": title, "url": url})
        content_text = normalise(html.unescape(re.sub(r"<[^>]+>", " ", content)))
    else:
        content_text = ""
    pending_marker = "Informacija ruošiama".casefold() in content_text.casefold()
    if documents:
        status = "published"
        note = f"Turinio dalyje rasta {len(documents)} dokumentų nuorod(a/os)."
    elif pending_marker:
        status = "pending"
        note = "VRK puslapio turinyje nurodyta „Informacija ruošiama“; dokumentų dar nėra."
    else:
        status = "unknown"
        note = (
            "Puslapio turinio dalyje nerasta nei dokumentų nuorodų, nei žymos "
            "„Informacija ruošiama“. Būseną reikia patikrinti rankiniu būdu."
        )
    return {
        "status": status,
        "published": status == "published",
        "documents": documents,
        "note": note,
        "pending_marker": pending_marker,
    }


def load_overlay(path: Path = OVERLAY) -> dict[str, Any] | None:
    if not path.exists():
        return None
    doc = json.loads(path.read_text("utf-8"))
    if doc.get("format") != OVERLAY_FORMAT:
        raise RegisterError("candidate-list overlay format invalid")
    return doc


def apply_candidate_lists(doc: dict[str, Any], overlay: dict[str, Any] | None) -> dict[str, Any]:
    """Merge a published candidate-list overlay into the participant register.

    The overlay is the only place where candidate names and ballot numbers may
    enter the document.  A non-published overlay leaves those fields empty.
    """
    if not overlay or overlay.get("status") != "published":
        return doc
    published_at = overlay.get("published_at")
    source_url = overlay.get("source_url")
    if not DATE_RE.fullmatch(str(published_at or "")):
        raise RegisterError("published candidate-list overlay needs published_at")
    if not str(source_url or "").startswith("https://"):
        raise RegisterError("published candidate-list overlay needs https source_url")
    by_id = {row["list_id"]: row for row in doc["vilnius"]["lists"]}
    seen: set[str] = set()
    for item in overlay.get("lists", []):
        list_id = item.get("list_id")
        row = by_id.get(list_id)
        if row is None:
            raise RegisterError(f"candidate-list overlay references unknown list: {list_id}")
        if list_id in seen:
            raise RegisterError(f"duplicate candidate-list overlay row: {list_id}")
        seen.add(list_id)
        outcome = item.get("registration_outcome", "registered")
        if outcome not in ("registered", "rejected"):
            raise RegisterError(f"{list_id}: invalid registration_outcome")
        row["registration_outcome"] = outcome
        if outcome == "rejected":
            row["candidate_list_published"] = False
            row["candidate_names_published"] = False
            row["ballot_number"] = None
            row["candidates"] = []
            row["rejection_reason"] = item.get("rejection_reason") or "VRK sprendimas neregistruoti"
            continue
        candidates = item.get("candidates")
        if not isinstance(candidates, list) or not candidates:
            raise RegisterError(f"{list_id}: published candidate list needs candidates")
        clean_candidates = []
        numbers: set[int] = set()
        for candidate in candidates:
            name = _clean(str(candidate.get("name", "")))
            number = candidate.get("number")
            if not name:
                raise RegisterError(f"{list_id}: candidate name required")
            if isinstance(number, bool) or not isinstance(number, int) or number < 1:
                raise RegisterError(f"{list_id}: candidate number must be a positive integer")
            if number in numbers:
                raise RegisterError(f"{list_id}: duplicate candidate number {number}")
            numbers.add(number)
            clean_candidates.append({"name": name, "number": number})
        ballot_number = item.get("ballot_number")
        if isinstance(ballot_number, bool) or not isinstance(ballot_number, int) or ballot_number < 1:
            raise RegisterError(f"{list_id}: ballot_number must be a positive integer")
        row["candidate_list_published"] = True
        row["candidate_names_published"] = True
        row["ballot_number"] = ballot_number
        row["candidates"] = clean_candidates
        row["registration_outcome"] = "registered"
        mayor = item.get("mayor_candidate")
        if mayor:
            if row.get("mayor_candidate") is None:
                raise RegisterError(f"{list_id}: overlay mayor has no campaign participant record")
            row["mayor_candidate"].update(
                {
                    "candidate_registered": True,
                    "ballot_number": mayor.get("ballot_number"),
                    "candidates_published": True,
                }
            )
    status = doc["official_status"]
    published_rows = [row for row in doc["vilnius"]["lists"] if row["candidate_list_published"]]
    if not published_rows:
        raise RegisterError("published candidate-list overlay produced no published lists")
    unreviewed = [
        row["list_id"]
        for row in doc["vilnius"]["lists"]
        if row.get("registration_outcome") == "registered" and not row["candidate_list_published"]
    ]
    if unreviewed:
        raise RegisterError("published overlay does not cover every registered list: " + ", ".join(unreviewed))
    status["candidate_lists_published"] = True
    status["candidate_names_published"] = True
    status["ballot_numbers_published"] = True
    status["candidate_lists_source_url"] = source_url
    status["candidate_lists_published_at"] = published_at
    status["status_note"] = (
        "VRK paskelbti galutiniai kandidatų sąrašai, pavardės ir numeriai. "
        "Šiame registre jie rodomi tik tada, kai juos patvirtina atskiras paskelbtų sąrašų įrašas."
    )
    doc["vilnius"]["list_participants_count"] = len(
        [row for row in doc["vilnius"]["lists"] if row.get("registration_outcome") != "rejected"]
    )
    doc["vilnius"]["mayor_participants_count"] = sum(
        1 for row in doc["vilnius"]["lists"] if row.get("mayor_candidate")
    )
    return doc


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
            index["retrieved_at"][:10],
            "2027 m. kampanijos dalyvių sąrašas pagal partijas",
        )
    ]

    for group in parsed["organisations"]:
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
                    detail["retrieved_at"][:10],
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
            calendar["retrieved_at"][:10],
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
            programmes["retrieved_at"][:10],
            "Kandidatų rinkimų programų leidiniai",
        )
    )
    programme_state = parse_publication_page(programmes["raw"].decode("utf-8", "replace"))

    ballot_page = fetch(BALLOT_URL)
    if ballot_page["http_status"] != 200:
        raise RegisterError(f"VRK ballot examples page unavailable: HTTP {ballot_page['http_status']}")
    rows.append(
        _manifest_row(
            ballot_page,
            "vrk_ballot_examples",
            "Vyriausioji rinkimų komisija",
            ballot_page["retrieved_at"][:10],
            "Biuletenių pavyzdžiai ir kiti leidiniai",
        )
    )
    ballot_state = parse_publication_page(ballot_page["raw"].decode("utf-8", "replace"))

    general = fetch(GENERAL_URL)
    if general["http_status"] == 200:
        rows.append(
            _manifest_row(
                general,
                "vrk_general",
                "Vyriausioji rinkimų komisija",
                general["retrieved_at"][:10],
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
                participants["retrieved_at"][:10],
                "Kampanijos dalyviai ir suinteresuotieji",
            )
        )

    snapshot_date = max(row["retrieved_at"][:10] for row in rows)
    lists = []
    for group in parsed["organisations"]:
        list_entry = group.get("list")
        if not list_entry:
            continue
        mayor = group.get("mayor")
        lists.append(
            {
                "list_id": group["organisation_id"],
                "party_id": group["party_id"],
                "organisation_id": group["organisation_id"],
                "organisation_name": group["organisation_name"],
                "organisation_kind": group["organisation_kind"],
                "list_name": list_entry["label"],
                "participant_number": list_entry["participant_number"],
                "registered_on": list_entry.get("registered_on"),
                "decision": list_entry.get("decision"),
                "status": list_entry.get("status"),
                "detail_url": list_entry["detail_url"],
                "registration_outcome": "registered",
                "mayor_candidate": (
                    {
                        "name": mayor["name"],
                        "registered_on": mayor.get("registered_on"),
                        "decision": mayor.get("decision"),
                        "status": mayor.get("status"),
                        "detail_url": mayor["detail_url"],
                        "campaign_participant": True,
                        "candidate_registered": False,
                        "candidates_published": False,
                        "ballot_number": None,
                    }
                    if mayor
                    else None
                ),
                "candidate_list_published": False,
                "candidate_names_published": False,
                "ballot_number": None,
                "candidates": [],
                "programme_publications": [],
            }
        )

    doc = {
        "format": FORMAT,
        "generated_at": snapshot_date,
        "snapshot": {
            "date": snapshot_date,
            "label": "VRK kampanijos dalyvių registro momentinė kopija",
            "immutable": True,
            "index_retrieved_at": index["retrieved_at"],
            "index_text_sha256": index["text_sha256"],
        },
        "election": {
            "id": "2027-municipal",
            "date": "2027-03-07",
            "municipality": "Vilnius",
            "municipality_code": "Vilniaus miesto savivaldybė",
            "apygarda": 57,
            "council_seats": 51,
            "official_page": GENERAL_URL,
            "final_lists_date": official_status["candidate_lists_published_by"]["date"],
            "preference_votes": 5,
        },
        "official_status": {
            **official_status,
            "candidate_lists_published": False,
            "candidate_names_published": False,
            "ballot_numbers_published": False,
            "candidate_lists_source_url": None,
            "candidate_lists_published_at": None,
            "programme_publications_published": programme_state["published"],
            "programme_publications_status": programme_state["status"],
            "programme_publications_note": programme_state["note"],
            "programme_publications_documents": programme_state["documents"],
            "programme_publications_url": PROGRAMMES_URL,
            "ballot_examples_published": ballot_state["published"],
            "ballot_examples_status": ballot_state["status"],
            "ballot_examples_note": ballot_state["note"],
            "ballot_examples_documents": ballot_state["documents"],
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
            "list_participants_count": len(lists),
            "mayor_participants_count": sum(1 for row in lists if row.get("mayor_candidate")),
            "lists": lists,
        },
        "source": {
            "index_url": INDEX_URL,
            "fixture": "fixtures/vrk_2027_dalyviai.html",
            "manifest": "vaa/review/vrk_2027_manifest.json",
            "index_sha256": index["content_sha256"],
            "index_text_sha256": index["text_sha256"],
            "index_retrieved_at": index["retrieved_at"],
            "snapshot_date": snapshot_date,
        },
        "limits": [
            "Tai kampanijos dalyvių registras, ne galutinis biuletenis.",
            f"Galutiniai kandidatų sąrašai ir numeriai VRK svetainėje turi būti paskelbti iki {official_status['candidate_lists_published_by']['date']}.",
            "Pateikiami kampanijos registre matomi asmenų vardai yra mero kampanijos dalyviai; jie nepatvirtina registracijos kandidatais. Galutinių kandidatų sąrašų pavardės ir biuletenio numeriai čia nepateikiami, kol VRK jų nepaskelbia.",
            f"Registracija gali keistis iki {official_status['participant_registration_ends']['date']}; šis failas yra {snapshot_date} būsena.",
        ],
    }
    overlay = load_overlay()
    if overlay:
        apply_candidate_lists(doc, overlay)
    validate_register(doc)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(
        json.dumps(
            {
                "format": MANIFEST_FORMAT,
                "generated_at": snapshot_date,
                "snapshot": doc["snapshot"],
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


def _require_date(value: Any, label: str) -> None:
    if not DATE_RE.fullmatch(str(value or "")):
        raise RegisterError(f"{label}: date required")


def validate_register(doc: dict[str, Any]) -> None:
    if doc.get("format") != FORMAT:
        raise RegisterError("invalid format")
    if doc.get("election", {}).get("date") != "2027-03-07":
        raise RegisterError("election date changed")
    if doc.get("election", {}).get("apygarda") != 57:
        raise RegisterError("Vilnius apygarda must be 57")
    _require_date(doc.get("generated_at"), "generated_at")
    status = doc.get("official_status")
    if not isinstance(status, dict):
        raise RegisterError("official_status required")
    for key in ("participant_registration_ends", "candidate_nomination_ends",
                "candidate_lists_decision_by", "candidate_lists_published_by"):
        if not isinstance(status.get(key), dict):
            raise RegisterError(f"missing official date: {key}")
        _require_date(status[key].get("date"), key)
    lists = doc.get("vilnius", {}).get("lists")
    if not isinstance(lists, list) or not lists:
        raise RegisterError("Vilnius registered lists required")
    seen_ids: set[str] = set()
    seen_party_ids: set[str] = set()
    published = 0
    rejected = 0
    for row in lists:
        list_id = row.get("list_id")
        if not isinstance(list_id, str) or not list_id:
            raise RegisterError("list_id required for each registered list")
        if list_id in seen_ids:
            raise RegisterError("duplicate list_id: " + list_id)
        seen_ids.add(list_id)
        party_id = row.get("party_id")
        if party_id is not None:
            if party_id in seen_party_ids:
                raise RegisterError("duplicate party list: " + party_id)
            seen_party_ids.add(party_id)
        if not row.get("organisation_name"):
            raise RegisterError(f"{list_id}: organisation_name required")
        _require_date(row.get("registered_on"), f"{list_id} registered_on")
        if not str(row.get("detail_url", "")).startswith("https://www.vrk.lt/"):
            raise RegisterError(f"{list_id}: official detail URL required")
        outcome = row.get("registration_outcome", "registered")
        if outcome not in ("registered", "rejected"):
            raise RegisterError(f"{list_id}: invalid registration_outcome")
        if outcome == "rejected":
            rejected += 1
            if row.get("candidate_list_published") or row.get("ballot_number") or row.get("candidates"):
                raise RegisterError(f"{list_id}: rejected list cannot have candidates or a ballot number")
        if row.get("candidate_list_published") is True:
            published += 1
            if outcome != "registered":
                raise RegisterError(f"{list_id}: rejected list cannot be marked published")
            ballot_number = row.get("ballot_number")
            if isinstance(ballot_number, bool) or not isinstance(ballot_number, int) or ballot_number < 1:
                raise RegisterError(f"{list_id}: ballot number required when list is published")
            candidates = row.get("candidates")
            if not isinstance(candidates, list) or not candidates:
                raise RegisterError(f"{list_id}: candidate names required when list is published")
            numbers = set()
            for candidate in candidates:
                name = str(candidate.get("name", "")).strip()
                number = candidate.get("number")
                if not name:
                    raise RegisterError(f"{list_id}: candidate name required")
                if isinstance(number, bool) or not isinstance(number, int) or number < 1:
                    raise RegisterError(f"{list_id}: candidate number required")
                if number in numbers:
                    raise RegisterError(f"{list_id}: duplicate candidate number")
                numbers.add(number)
            if row.get("candidate_names_published") is not True:
                raise RegisterError(f"{list_id}: candidate names must be marked published")
        else:
            if row.get("ballot_number") is not None:
                raise RegisterError(f"{list_id}: ballot number must remain null until VRK publishes it")
            if row.get("candidates"):
                raise RegisterError(f"{list_id}: candidate names must remain empty until VRK publishes them")
            if row.get("candidate_names_published") is True:
                raise RegisterError(f"{list_id}: candidate names cannot be claimed as published")
        mayor = row.get("mayor_candidate")
        if mayor:
            if not mayor.get("name"):
                raise RegisterError(f"{list_id}: mayor campaign participant name required")
            if mayor.get("candidate_registered") is True:
                if not row.get("candidate_list_published"):
                    raise RegisterError(f"{list_id}: mayor candidate registration needs a published list")
                ballot_number = mayor.get("ballot_number")
                if isinstance(ballot_number, bool) or not isinstance(ballot_number, int) or ballot_number < 1:
                    raise RegisterError(f"{list_id}: mayor ballot number required when registered")
    if doc["vilnius"].get("list_participants_count") != len(lists) - rejected:
        raise RegisterError("list participant count does not match the list rows")
    expected_mayors = sum(1 for row in lists if row.get("mayor_candidate"))
    if doc["vilnius"].get("mayor_participants_count") != expected_mayors:
        raise RegisterError("mayor participant count does not match the list rows")
    if doc.get("snapshot", {}).get("date") != doc.get("generated_at"):
        raise RegisterError("snapshot date must match generated_at")
    if doc.get("election", {}).get("final_lists_date") != status["candidate_lists_published_by"]["date"]:
        raise RegisterError("election final_lists_date does not match the official calendar")
    preference_votes = doc.get("election", {}).get("preference_votes")
    if isinstance(preference_votes, bool) or not isinstance(preference_votes, int) or not 1 <= preference_votes <= 5:
        raise RegisterError("preference_votes must be an integer from 1 to 5")
    if status.get("candidate_lists_published") is True:
        if published == 0:
            raise RegisterError("candidate lists cannot be marked published without a published list")
        if published + rejected != len(lists):
            raise RegisterError("every list must be published or rejected once final lists are published")
        for key in ("candidate_names_published", "ballot_numbers_published"):
            if status.get(key) is not True:
                raise RegisterError(f"{key} must be true when candidate lists are published")
        _require_date(status.get("candidate_lists_published_at"), "candidate_lists_published_at")
        if not str(status.get("candidate_lists_source_url", "")).startswith("https://"):
            raise RegisterError("candidate list source URL required")
    else:
        if published:
            raise RegisterError("a list cannot be published while candidate lists are marked unpublished")
        for key in ("candidate_names_published", "ballot_numbers_published"):
            if status.get(key) is not False:
                raise RegisterError(f"this register must not claim {key}")
    for key in ("programme_publications", "ballot_examples"):
        published_flag = f"{key}_published"
        status_key = f"{key}_status"
        documents_key = f"{key}_documents"
        state = status.get(status_key)
        if state not in ("pending", "published", "unknown"):
            raise RegisterError(f"{key}: invalid publication status")
        if status.get(published_flag) is not (state == "published"):
            raise RegisterError(f"{key}: published flag does not match status")
        documents = status.get(documents_key)
        if not isinstance(documents, list):
            raise RegisterError(f"{key}: document list required")
        if state == "published" and not documents:
            raise RegisterError(f"{key}: published state needs at least one document")
        if state != "published" and documents:
            raise RegisterError(f"{key}: documents cannot exist unless published")
        if not str(status.get(f"{key}_url", "")).startswith("https://"):
            raise RegisterError(f"{key}: source URL required")
    source = doc.get("source", {})
    if not re.fullmatch(r"[0-9a-f]{64}", source.get("index_sha256", "")):
        raise RegisterError("index hash required")
    _require_date(doc.get("snapshot", {}).get("date"), "snapshot date")


def check() -> None:
    doc = json.loads(OUT.read_text("utf-8"))
    validate_register(doc)
    manifest = json.loads(MANIFEST.read_text("utf-8"))
    if manifest.get("format") != MANIFEST_FORMAT:
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
    print(
        "OK: {n} Vilnius lists, {m} mayor campaign participants, candidate lists published: {p}, final lists expected by {d}".format(
            n=doc["vilnius"]["list_participants_count"],
            m=doc["vilnius"]["mayor_participants_count"],
            p=doc["official_status"]["candidate_lists_published"],
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
            f"  {row['list_id']:28} registered {row['registered_on']}  "
            f"mayor campaign participant: {mayor['name'] if mayor else '—'}  "
            f"list number: {row['ballot_number'] or '—'}"
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
        print("wrote", OUT.relative_to(ROOT), "with", doc["vilnius"]["list_participants_count"], "lists")
    if args.check:
        check()
    if args.show:
        print_summary()
    if not (args.refresh or args.check or args.show):
        parser.print_help()


if __name__ == "__main__":
    main()
