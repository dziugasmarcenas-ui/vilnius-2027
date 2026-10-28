#!/usr/bin/env python3
"""Build the public v21 pages and a clean static deployment folder.

The build reads only curated files:
  - vaa/vilnius_2027_v21.json
  - vaa/vilnius_2027_register.json
  - vaa/review/source_manifest_v21.json
  - vaa/review/source_checks_v21.json
  - vaa/manobalsas_2024_context.json

It never copies .cache/, scraped HTML, personal notes, or research drafts into
dist/.  The output is intentionally static: no server, analytics, cookies, or
external scripts.
"""
from __future__ import annotations

import argparse
import html
import json
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from vaa_v21 import VaaError, check_evidence, load_data

ROOT = Path(__file__).resolve().parent
QUIZ_TEMPLATE = ROOT / "templates" / "quiz_v21.template.html"
STATIC_TEMPLATE = ROOT / "templates" / "static_v21.template.html"
ENGINE = ROOT / "vaa_engine_v21.js"
REGISTER = ROOT / "vaa" / "vilnius_2027_register.json"
LAW = ROOT / "vaa" / "law_2027.json"
SOURCES = ROOT / "vaa" / "sources_2027_v21.json"
MANIFEST = ROOT / "vaa" / "review" / "source_manifest_v21.json"
CHECKS = ROOT / "vaa" / "review" / "source_checks_v21.json"
MB_CONTEXT = ROOT / "vaa" / "manobalsas_2024_context.json"
HUMAN = ROOT / "vaa" / "review" / "human_validation_v21.json"
GAPS = ROOT / "vaa" / "review" / "coverage_gaps_v21.json"
DIST = ROOT / "dist"
DEFAULT_CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:"
SCRIPT_CSP = "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:"
CALENDAR_URL = "https://vrk.lt/fs/prd/p/2026-10/Rinkimu%20grafikas%202027.pdf"

PAGES = {
    "index.html": "index",
    "quiz_v21.html": "quiz",
    "register_2027.html": "register",
    "sources_2027.html": "sources",
    "methodology_2027.html": "methodology",
    "ballot_2027.html": "ballot",
    "rinkejui_2027.html": "voter",
    "compare_2027.html": "compare",
    "review_2027.html": "review",
}
DATA_FILES = [
    "vilnius_2027_v21.json",
    "vilnius_2027_register.json",
    "sources_2027_v21.json",
    "manobalsas_2024_context.json",
]
REVIEW_FILES = ["source_manifest_v21.json", "source_checks_v21.json", "human_validation_v21.json", "coverage_gaps_v21.json", "vrk_2027_manifest.json"]


class BuildError(ValueError):
    pass


def e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def ext(url: str, text: str, cls: str = "") -> str:
    cls_attr = f' class="{e(cls)}"' if cls else ""
    return (
        f'<a href="{e(url)}"{cls_attr} target="_blank" rel="noopener noreferrer">'
        f"{e(text)}</a>"
    )


def internal(url: str, text: str, cls: str = "") -> str:
    cls_attr = f' class="{e(cls)}"' if cls else ""
    return f'<a href="{e(url)}"{cls_attr}>{e(text)}</a>'


def chip(text: str, cls: str = "") -> str:
    return f'<span class="chip {e(cls)}">{e(text)}</span>'


def json_script(doc: Any) -> str:
    text = json.dumps(doc, ensure_ascii=False, separators=(",", ":"))
    return text.replace("</", "<\\/")


def load_human() -> dict[str, Any]:
    human = json.loads(HUMAN.read_text("utf-8"))
    if human.get("format") != "vaa-human-validation-v21":
        raise BuildError("human validation format invalid")
    if human.get("status") != "pending":
        raise BuildError("human validation must remain pending until a named review is complete")
    if human.get("public_use_allowed") is not False:
        raise BuildError("public use cannot be allowed while review is pending")
    if not human.get("checklist"):
        raise BuildError("human validation checklist required")
    for item in human["checklist"]:
        if item.get("status") not in ("pending", "blocked"):
            raise BuildError(f"unexpected human validation checklist status: {item.get('id')}")
    return human


def load_all() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    data = load_data()
    check_evidence(data)
    register = json.loads(REGISTER.read_text("utf-8"))
    if register.get("format") != "vaa-vrk-2027-register-v2":
        raise BuildError("register format invalid")
    lists = register.get("vilnius", {}).get("lists", [])
    if not lists:
        raise BuildError("register must contain at least one Vilnius list")
    if register.get("official_status", {}).get("candidate_lists_published") is True:
        # A published list becomes renderable only after the overlay validator
        # has accepted candidate names and ballot numbers.
        for row in lists:
            if row.get("registration_outcome") == "registered" and not row.get("candidate_list_published"):
                raise BuildError("published register leaves a registered list without candidates")
    law = json.loads(LAW.read_text("utf-8"))
    if law.get("format") != "vaa-law-registry-v1":
        raise BuildError("law registry format invalid")
    sources = json.loads(SOURCES.read_text("utf-8"))
    manifest = json.loads(MANIFEST.read_text("utf-8"))
    checks = json.loads(CHECKS.read_text("utf-8"))
    mb = json.loads(MB_CONTEXT.read_text("utf-8"))
    if manifest.get("format") != "vaa-source-manifest-v21":
        raise BuildError("manifest format invalid")
    if checks.get("format") != "vaa-source-checks-v21":
        raise BuildError("checks format invalid")
    if mb.get("format") != "vaa-manobalsas-context-v1" or mb.get("status") != "context_only":
        raise BuildError("Mano Balsas context invalid")
    for source in sources.get("sources", []):
        if not str(source.get("url", "")).startswith("https://"):
            raise BuildError("source url must be https")
    if "rinkimu_kodeksas_2027" not in {source.get("id") for source in sources.get("sources", [])}:
        raise BuildError("legal source missing from registry")
    return data, register, sources, manifest, checks, mb, law


def _list_display_name(row: dict[str, Any], parties: dict[str, dict[str, Any]]) -> str:
    party = parties.get(row.get("party_id")) if row.get("party_id") else None
    return (party or {}).get("short_name") or row.get("organisation_name") or row.get("list_id") or "—"


def _list_full_name(row: dict[str, Any], parties: dict[str, dict[str, Any]]) -> str:
    party = parties.get(row.get("party_id")) if row.get("party_id") else None
    return (party or {}).get("name") or row.get("organisation_name") or row.get("list_name") or "—"


def sync_party_mayors(data: dict[str, Any], register: dict[str, Any]) -> dict[str, Any]:
    """Bind quiz party labels to the current register snapshot.

    The dataset may carry older wording; the register is authoritative for who
    is a *campaign participant*.  This never turns a participant into a
    registered election candidate.
    """
    import copy

    synced = copy.deepcopy(data)
    by_party = {
        row.get("party_id"): row
        for row in register.get("vilnius", {}).get("lists", [])
        if row.get("party_id")
    }
    snapshot = register.get("snapshot", {}).get("date") or register["generated_at"]
    for party in synced.get("parties", []):
        row = by_party.get(party["id"])
        mayor = (row or {}).get("mayor_candidate")
        if mayor and mayor.get("name"):
            party["mayor_candidate"] = mayor["name"].title()
            party["mayor_note"] = (
                f"VRK registre {snapshot} buvo mero kampanijos dalyvis; "
                "tai nepatvirtina registracijos mero rinkimų kandidatu."
            )
        else:
            party["mayor_candidate"] = None
            party["mayor_note"] = (
                f"VRK registre {snapshot} nebuvo šios organizacijos mero kampanijos dalyvio įrašo. "
                "Tai nereiškia, kad kandidatas nebus registruotas vėliau."
            )
    return synced


def public_register(register: dict[str, Any]) -> dict[str, Any]:
    """Remove the local fixture path before publishing the register snapshot."""
    import copy

    doc = copy.deepcopy(register)
    source = doc.get("source")
    if isinstance(source, dict):
        source.pop("fixture", None)
    return doc


def trimmed_register(register: dict[str, Any]) -> dict[str, Any]:
    vilnius = register["vilnius"]
    lists = []
    for row in vilnius["lists"]:
        mayor = row.get("mayor_candidate")
        lists.append(
            {
                "list_id": row.get("list_id"),
                "party_id": row.get("party_id"),
                "party_name": row.get("organisation_name"),
                "list_name": row.get("list_name"),
                "registered_on": row.get("registered_on"),
                "decision": row.get("decision"),
                "detail_url": row.get("detail_url"),
                "registration_outcome": row.get("registration_outcome", "registered"),
                "mayor_candidate": (
                    {
                        "name": mayor.get("name"),
                        "registered_on": mayor.get("registered_on"),
                        "decision": mayor.get("decision"),
                        "detail_url": mayor.get("detail_url"),
                        "candidate_registered": bool(mayor.get("candidate_registered")),
                        "ballot_number": mayor.get("ballot_number"),
                    }
                    if mayor
                    else None
                ),
                "candidate_list_published": bool(row.get("candidate_list_published")),
                "candidate_names_published": bool(row.get("candidate_names_published")),
                "ballot_number": row.get("ballot_number"),
                "candidates": row.get("candidates") or [],
            }
        )
    return {
        "election": register["election"],
        "special": {
            "snapshot_date": register.get("snapshot", {}).get("date") or register["generated_at"],
        },
        "official_status": register["official_status"],
        "vilnius": {
            "apygarda": vilnius["apygarda"],
            "list_participants_count": vilnius["list_participants_count"],
            "mayor_participants_count": vilnius["mayor_participants_count"],
            "lists": lists,
        },
        "national": {"participants_count": register.get("national", {}).get("participants_count")},
        "source": public_register(register)["source"],
        "limits": register["limits"],
    }


def make_meta(
    data: dict[str, Any],
    register: dict[str, Any],
    manifest: dict[str, Any],
    checks: dict[str, Any],
    mb: dict[str, Any],
) -> dict[str, Any]:
    scoreable = sum(
        1
        for rows in data["answers"].values()
        for row in rows.values()
        if row.get("scoreable") is True
    )
    non_scoreable = sum(
        1
        for rows in data["answers"].values()
        for row in rows.values()
        if row.get("stance") != "unknown" and row.get("scoreable") is not True
    )
    unknown = sum(
        1
        for rows in data["answers"].values()
        for row in rows.values()
        if row.get("stance") == "unknown"
    )
    known = sum(
        1
        for rows in data["answers"].values()
        for row in rows.values()
        if row.get("stance") != "unknown"
    )
    quoted = sum(
        1
        for rows in data["answers"].values()
        for row in rows.values()
        if row.get("quote")
    )
    return {
        "generated_at": data["generated_at"],
        "register_generated_at": register["generated_at"],
        "snapshot_date": register.get("snapshot", {}).get("date") or register["generated_at"],
        "final_lists_date": register.get("official_status", {}).get("candidate_lists_published_by", {}).get("date", "2027-02-05"),
        "preference_votes": register.get("election", {}).get("preference_votes", 5),
        "source_checked_at": checks["generated_at"],
        "source_count": len(manifest["sources"]),
        "dataset_quote_count": quoted,
        "dataset_scoreable_count": scoreable,
        "dataset_known_count": known,
        "check_count": len(checks["checks"]),
        "human_validation": data["human_validation"],
        "manobalsas": {
            "title": mb["methodology"]["title"],
            "status": mb["status"],
            "methodology_url": mb["methodology"]["url"],
            "test_url": mb["test"]["url"],
            "question_count": mb["test"]["question_count"],
            "dimensions": mb["test"]["dimensions"],
        },
        "counts": {
            "topics": len(data["topics"]),
            "statements": len(data["statements"]),
            "parties": len(data["parties"]),
            "evidence_rows": quoted,
            "scoreable_rows": scoreable,
            "non_scoreable_rows": non_scoreable,
            "unknown_rows": unknown,
        },
    }


def party_map(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {p["id"]: p for p in data["parties"]}


def stance_label(stance: str) -> str:
    return {
        "agree": "Visiškai sutinku",
        "lean_agree": "Greičiau sutinku",
        "neutral": "Nei taip, nei ne",
        "lean_disagree": "Greičiau nesutinku",
        "disagree": "Visiškai nesutinku",
        "unknown": "Nėra pozicijos",
    }.get(stance, stance)


def status_banner(data: dict[str, Any], register: dict[str, Any]) -> str:
    st = register["official_status"]
    final_date = st["candidate_lists_published_by"]["date"]
    return f"""
    <div class="banner">
      <strong>Oficialus 2027 m. biuletenis dar nepaskelbtas.</strong>
      VRK galutinius kandidatų sąrašus ir sąrašų numerius numato paskelbti iki {e(final_date)}.
      Šiandien lyginame {len(register['vilnius']['lists'])} Vilniaus miesto sąrašų kampanijos dalyvių įrašus ir tik turimus 2027 m. programinius dokumentus.
      {internal('register_2027.html', 'Žiūrėti registrą →')}
    </div>
    """


def pending_banner(register: dict[str, Any]) -> str:
    st = register["official_status"]
    final_date = st["candidate_lists_published_by"]["date"]
    return f"""
    <div class="banner">
      <strong>Oficialus 2027 m. biuletenis dar nepaskelbtas.</strong>
      VRK galutinius kandidatų sąrašus ir sąrašų numerius numato paskelbti iki {e(final_date)}.
      {internal('register_2027.html', 'Žiūrėti registrą →')}
    </div>
    """


def _timeline_class(index: int, snapshot: str) -> str:
    return "done" if index == 0 else ("next" if index == 1 else "future")


def index_content(
    data: dict[str, Any],
    register: dict[str, Any],
    sources: dict[str, Any],
    checks: dict[str, Any],
) -> str:
    parties = party_map(data)
    lists = register["vilnius"]["lists"]
    st = register["official_status"]
    snapshot = register.get("snapshot", {}).get("date") or register["generated_at"]
    list_count = register["vilnius"]["list_participants_count"]
    mayor_count = register["vilnius"]["mayor_participants_count"]
    final_date = st["candidate_lists_published_by"]["date"]
    list_rows = []
    for row in lists:
        party = parties.get(row.get("party_id")) if row.get("party_id") else None
        mayor = row.get("mayor_candidate")
        if row.get("registration_outcome") == "rejected":
            mayor_text = "<b>—</b><span>sąrašas neregistruotas</span>"
        elif mayor:
            if mayor.get("candidate_registered"):
                mayor_text = f"<b>{e(mayor['name'].title())}</b><span>registruotas mero kandidatas</span>"
            else:
                mayor_text = f"<b>{e(mayor['name'].title())}</b><span>mero kampanijos dalyvis</span>"
        else:
            mayor_text = "<b>—</b><span>mero kampanijos dalyvio įrašo šiame rinkinyje nėra</span>"
        list_rows.append(
            f"""
            <div class="party-row">
              <span class="party-dot" style="background:{e((party or {}).get('color', '#777'))}"></span>
              <div><div class="name">{e(_list_display_name(row, parties))}</div>
              <div class="full">{e(_list_full_name(row, parties))}</div></div>
              <div class="mayor">{mayor_text}</div>
            </div>
            """
        )
    feature_cards = [
        (
            "01",
            f"{list_count} sąrašų kampanijos įrašai",
            f"Rinkinyje yra {list_count} sąrašų kampanijos dalyvių įrašai iš {snapshot} VRK momentinės kopijos. Tai nėra kandidatų registracijos patvirtinimas.",
        ),
        (
            "02",
            "Citata, data, nuoroda",
            "Kiekviena įskaitoma pozicija turi verbatim citatą ir šaltinį. Nežinoma pozicija nėra priešingybė ir negauna neigiamo balo.",
        ),
        (
            "03",
            "Sprendimas pagal temą",
            "Rodome ne vieną paslaptingą reitingą, o programų atitikimą tema po temos, aprėptį ir trūkstamus duomenis.",
        ),
        (
            "04",
            "Balsavimo planas",
            "Paaiškiname tarybos ir mero biuletenių struktūrą pagal galiojančią Rinkimų kodekso redakciją, bet aiškiai pasakome, kad oficialūs pavyzdžiai dar nepaskelbti.",
        ),
    ]
    feature_html = "".join(
        f'<div class="card flat"><div class="kicker">{e(n)}</div><h2 class="feature-title">{e(t)}</h2><p>{e(d)}</p></div>'
        for n, t, d in feature_cards
    )
    return f"""
    <section class="hero">
      <span class="eyebrow"><span class="dot"></span>Vilnius 2027 · taryba ir meras</span>
      <h1>Ką apie Vilnių žada partijos – ir ką tai reiškia tau?</h1>
      <p class="lead">Atsakyk į 13 teiginių apie transportą, švietimą, būstą, skaidrumą, saugumą ir verslą. Parodysime, kurie kampanijos dalyvių registre esantys sąrašų įrašai turi cituojamas 2027 m. pozicijas ir kiek jų programos tekstas sutampa su tavo prioritetais.</p>
      <div class="actions">
        {internal('quiz_v21.html', 'Pradėti klausimyną →', 'btn btn-primary')}
        {internal('register_2027.html', 'Oficialus VRK registras', 'btn btn-ghost')}
        {internal('methodology_2027.html', 'Kaip skaičiuojame', 'btn btn-ghost')}
      </div>
      {status_banner(data, register)}
    </section>

    <section class="grid grid-2 mt2" aria-label="Ką darome">
      {feature_html}
    </section>

    <section class="card mt3">
      <div class="kicker">Dabartinė eiga</div>
      <h2>Kodėl tai dar ne galutinis biuletenis</h2>
      <p>VRK 2027 m. savivaldybių rinkimų ciklas dar nepasiekė galutinių kandidatų sąrašų. Šiame etape galime sąžiningai lyginti tik į šį rinkinį įtrauktų organizacijų paskelbtus programinius dokumentus.</p>
      <ol class="timeline">
        <li class="done"><b>2026-10-07</b><span>Prasidėjo politinė kampanija. VRK registre yra pirmieji sąrašų kampanijos dalyvių įrašai.</span></li>
        <li class="next"><b>{e(st['participant_registration_ends']['date'])}</b><span>Baigiamas savarankiškų kampanijos dalyvių registravimas. Šis registras fiksuoja tą būseną.</span></li>
        <li class="future"><b>{e(st['candidate_nomination_ends']['date'])}</b><span>Baigiamas kandidatų pareiškinių dokumentų pateikimas.</span></li>
        <li class="future"><b>{e(st['candidate_lists_decision_by']['date'])}</b><span>VRK priima sprendimus registruoti kandidatų sąrašus arba jų neregistruoti.</span></li>
        <li class="future"><b>{e(final_date)}</b><span>VRK paskelbia kandidatų sąrašus, kandidatus ir sąrašų rinkimų numerius.</span></li>
        <li class="future"><b>2027-03-07</b><span>Savivaldybių tarybų ir merų rinkimai.</span></li>
      </ol>
      <p class="small mt1">Datos iš oficialaus VRK 2027 m. rinkimų grafiko. {ext(st['candidate_lists_published_by']['source_url'], 'VRK grafikas')}</p>
    </section>

    <section class="card mt2">
      <div class="kicker">Sąrašų kampanijos įrašai</div>
      <h2>{list_count} sąrašai, {mayor_count} mero kampanijos dalyviai</h2>
      <p>Tai VRK {e(snapshot)} registracijų būsena, o ne galutinis biuletenis. Kandidatų pavardžių ir sąrašų numerių dar nėra.</p>
      <div class="party-list">{''.join(list_rows)}</div>
      <div class="actions">
        {internal('register_2027.html', 'Visa registracijų lentelė', 'btn btn-soft btn-sm')}
        {internal('sources_2027.html', 'Šaltiniai ir patikra', 'btn btn-ghost btn-sm')}
      </div>
    </section>

    <section class="grid grid-2 mt3">
      <div class="card">
        <div class="kicker">Skaidrumas</div>
        <h2>Ką galima, o ko negalima teigti</h2>
        <ul class="list-clean">
          <li><strong>Galima:</strong> cituoti šiandien paskelbtas 2027 m. partijų pozicijas ir lyginti jas su tavo atsakymais.</li>
          <li><strong>Galima:</strong> pasakyti, kad vieno sąrašo programos tekstas pagal turimus duomenis labiau atitinka tavo prioritetus.</li>
          <li><strong>Negalima:</strong> teigti, kad tai galutinis biuletenis, rinkimų prognozė, mandatų prognozė ar oficiali rekomendacija.</li>
          <li><strong>Negalima:</strong> nežinomos pozicijos versti neigiamu balu arba priskirti partijai pažado, kurio ji nepaskelbė.</li>
        </ul>
        <div class="inline-links">
          {internal('methodology_2027.html', 'Metodika ir ribos', 'btn btn-soft btn-sm')}
          {internal('ballot_2027.html', 'Kaip balsuoti', 'btn btn-ghost btn-sm')}
        </div>
      </div>
      <div class="card">
        <div class="kicker">Skaičiai</div>
        <h2>Kas šiandien patikrinta</h2>
        <ul class="status-list">
          <li><span>Sąrašų kampanijos įrašai</span><b>{list_count}</b></li>
          <li><span>Asmenų mero kampanijos įrašai</span><b>{mayor_count}</b></li>
          <li><span>Dabartiniai šaltiniai</span><b>{len(sources['sources'])}</b></li>
          <li><span>Patikrintos citatos</span><b>{len(checks['checks'])}</b></li>
          <li><span>Nepriklausoma žmogaus peržiūra</span><b>dar neatlikta</b></li>
        </ul>
        <p class="small mt2">Skaičiai kinta kartu su VRK registru ir partijų publikacijomis. Prieš naudodami rezultatą patikrinkite oficialų VRK biuletenį.</p>
      </div>
    </section>
    """


def register_content(data: dict[str, Any], register: dict[str, Any], checks: dict[str, Any]) -> str:
    parties = party_map(data)
    st = register["official_status"]
    snapshot = register.get("snapshot", {}).get("date") or register["generated_at"]
    list_count = register["vilnius"]["list_participants_count"]
    mayor_count = register["vilnius"]["mayor_participants_count"]
    final_date = st["candidate_lists_published_by"]["date"]
    candidate_lists_published = bool(st.get("candidate_lists_published"))
    rows = []
    for row in register["vilnius"]["lists"]:
        party = parties.get(row.get("party_id")) if row.get("party_id") else None
        mayor = row.get("mayor_candidate")
        if mayor and mayor.get("candidate_registered"):
            mayor_cell = (
                f"{e(mayor['name'].title())}<br><span class='small'>registruotas kandidatas"
                + (f", Nr. {e(mayor['ballot_number'])}" if mayor.get("ballot_number") else "")
                + "</span>"
            )
        elif mayor:
            mayor_cell = f"{e(mayor['name'].title())}<br><span class='small'>mero kampanijos dalyvis, ne rinkimų kandidatas</span>"
        else:
            mayor_cell = "—<br><span class='small'>įrašo nėra</span>"
        if row.get("registration_outcome") == "rejected":
            number_cell = "—<br><span class='small'>sąrašas neregistruotas</span>"
            names_cell = "—<br><span class='small'>nėra sąrašo</span>"
        elif row.get("candidate_list_published"):
            number_cell = f"<b>{e(row.get('ballot_number'))}</b>"
            candidates = row.get("candidates") or []
            names = ", ".join(
                f"{e(candidate['name'].title())} ({e(candidate['number'])})" for candidate in candidates
            )
            names_cell = (
                f"<details><summary>{len(candidates)} pavardės</summary>"
                f"<div class='small mt1'>{names}</div></details>"
            )
        else:
            number_cell = "—<br><span class='small'>nepaskelbtas</span>"
            names_cell = "—<br><span class='small'>nepaskelbtos</span>"
        rows.append(
            f"""
            <tr>
              <td><strong>{e(_list_display_name(row, parties))}</strong><br><span class="small">{e(_list_full_name(row, parties))}</span></td>
              <td>{e(row.get('registered_on'))}<br><span class="small">{e(row.get('decision'))}</span></td>
              <td>{mayor_cell}</td>
              <td>{number_cell}</td>
              <td>{names_cell}</td>
              <td>{ext(row['detail_url'], 'VRK įrašas')}</td>
            </tr>
            """
        )
    def publication_value(key: str, published_label: str, pending_label: str) -> tuple[str, str]:
        state = st.get(f"{key}_status")
        documents = st.get(f"{key}_documents") or []
        if state == "published" and documents:
            links = ", ".join(ext(doc["url"], doc.get("title") or "dokumentas") for doc in documents[:3])
            return published_label, links
        if state == "pending":
            return pending_label, ext(st[f"{key}_url"], "VRK puslapis")
        return "nepatikrinta", ext(st[f"{key}_url"], "VRK puslapis")

    programme_value, programme_note = publication_value(
        "programme_publications", "paskelbti", "Informacija ruošiama"
    )
    ballot_value, ballot_note = publication_value(
        "ballot_examples", "paskelbti", "Informacija ruošiama"
    )
    statuses = [
        (
            "Galutiniai kandidatų sąrašai",
            "paskelbti" if candidate_lists_published else "dar nepaskelbti",
            ext(st["candidate_lists_source_url"], st.get("candidate_lists_published_at") or "")
            if candidate_lists_published and st.get("candidate_lists_source_url")
            else f"iki {final_date}",
        ),
        (
            "Galutinių sąrašų kandidatų pavardės",
            "paskelbtos" if st.get("candidate_names_published") else "dar nepaskelbtos",
            f"iki {final_date}",
        ),
        (
            "Sąrašų rinkimų numeriai",
            "paskelbti" if st.get("ballot_numbers_published") else "dar nepaskelbti",
            f"iki {final_date}",
        ),
        ("Kandidatų programų leidiniai", programme_value, programme_note),
        ("Biuletenių pavyzdžiai", ballot_value, ballot_note),
    ]
    status_html = "".join(
        f"<li><span>{e(label)}</span><div><b>{e(value)}</b><span class='small'>{value2}</span></div></li>"
        for label, value, value2 in statuses
    )
    return f"""
    <section class="hero">
      <span class="eyebrow"><span class="dot"></span>Oficialus registras · {e(snapshot)}</span>
      <h1>Kas šiandien tikrai užregistruota?</h1>
      <p class="lead">{e(snapshot)} kopijoje yra {list_count} Vilniaus miesto sąrašų kampanijos dalyvių įrašai. Registracija kampanijos dalyviu nepatvirtina kandidato ar kandidatų sąrašo registracijos rinkimams.</p>
      {status_banner(data, register)}
    </section>

    <section class="grid grid-2">
      <div class="card">
        <div class="kicker">Būsena</div>
        <h2>Ko dar nėra</h2>
        <ul class="status-list">{status_html}</ul>
        <p class="small mt2">„Informacija ruošiama“ reiškia, kad VRK puslapis egzistuoja, bet dokumentų dar nėra. Tai nėra teigiamas patvirtinimas, kad dokumentai paskelbti.</p>
      </div>
      <div class="card">
        <div class="kicker">Kodėl tai svarbu</div>
        <h2>Dalyvis ≠ kandidatas ≠ biuletenis</h2>
        <p><strong>Kampanijos dalyvis</strong> – kampanijai registruota organizacija, asmuo ar sąrašo kampanijos įrašas. <strong>Kandidatas ar kandidatų sąrašas</strong> – atskiru rinkimų komisijos sprendimu rinkimams registruojamas asmuo ar sąrašas. <strong>Biuletenis</strong> – galutinis dokumentas su numeriais ir pavardėmis, kurį rinkėjas mato balsavimo vietoje.</p>
        <p class="small">Šiame etape galime sąžiningai lyginti tik rinkinyje esančių organizacijų paskelbtas 2027 m. pozicijas. Negalime išgalvoti kandidatų, numerių ar vietų sąraše.</p>
        <div class="inline-links">
          {internal('sources_2027.html', 'Registro šaltiniai', 'btn btn-soft btn-sm')}
          {internal('quiz_v21.html', 'Grįžti į klausimyną', 'btn btn-ghost btn-sm')}
        </div>
      </div>
    </section>

    <section class="card mt2">
      <div class="kicker">Vilniaus miesto apygarda Nr. 57</div>
      <h2>Kampanijos dalyvių sąrašai</h2>
      <div class="table-wrap">
        <table>
          <caption class="sr-only">Vilniaus miesto 2027 m. kampanijos dalyvių sąrašai ir mero kampanijos dalyviai</caption>
          <thead><tr><th>Sąrašas</th><th>Registracija</th><th>Mero kampanijos dalyvis</th><th>Sąrašo numeris</th><th>Kandidatų pavardės</th><th>Šaltinis</th></tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
      </div>
      <p class="small mt2">Lentelėje – VRK {e(snapshot)} būsena. Kandidatų sąrašai ir numeriai bus paskelbti atskirai; tada ši lentelė turi būti atnaujinta.</p>
    </section>

    <section class="card mt2">
      <div class="kicker">Oficialūs šaltiniai</div>
      <h2>Ką tikrinome</h2>
      <ul class="list-clean">
        <li>{ext(register['election']['official_page'], '2027 m. rinkimų bendra informacija')} – rinkimų data, apygarda, tarybos narių skaičius.</li>
        <li>{ext(st['candidate_lists_published_by']['source_url'], 'VRK 2027 m. rinkimų grafikas')} – registracijos, nominacijos ir paskelbimo datos.</li>
        <li>{ext(register['source']['index_url'], 'Kampanijos dalyvių sąrašas')} – {register.get('national', {}).get('participants_count', '—')} nacionaliniai dalyviai, {list_count} Vilniaus miesto sąrašai.</li>
        <li>{ext(st['programme_publications_url'], 'Kandidatų rinkimų programų leidiniai')} – {e(programme_value)}.</li>
        <li>{ext(st['ballot_examples_url'], 'Biuletenių pavyzdžiai ir kiti leidiniai')} – {e(ballot_value)}.</li>
      </ul>
      <p class="small mt2">Registro šaltinio SHA-256 hash’ai ir HTTP metadata publikuojami {internal('data/vrk_2027_manifest.json', 'VRK registro manifeste')}. Žalias scraped HTML lieka privačiame peržiūros pakete ir nėra publikuojamas. Citatų patikra – {len(checks['checks'])} eilutės.</p>
    </section>

    <section class="card mt2">
      <div class="kicker">Ribos</div>
      <h2>Ko šis registras negali pasakyti</h2>
      <ul class="list-clean">
        <li>Negali pasakyti, kas bus galutiniame biuletenyje – sąrašai dar gali keistis iki {e(st['candidate_lists_decision_by']['date'])}.</li>
        <li>Negali pasakyti sąrašo numerio – VRK jį paskelbs kartu su galutiniais sąrašais.</li>
        <li>Negali pasakyti, ar partija turi poziciją konkrečiu klausimu – tam reikia atskiros citatos.</li>
        <li>Negali pakeisti oficialaus VRK biuletenio balsavimo vietoje.</li>
      </ul>
    </section>
    """


INTERPRETATION_LABELS = {
    "government_level": {
        "local": "savivaldybė",
        "national": "nacionalinis",
        "mixed": "mišrus",
        "unclear": "neaiškus",
        "not_applicable": "netaikoma",
    },
    "document_status": {
        "draft": "programos projektas",
        "adopted": "patvirtinta programa",
        "campaign_statement": "kampanijos pareiškimas",
        "not_found": "pozicijos nerasta",
    },
    "speaker_authority": {
        "party": "partija",
        "candidate": "kandidatas",
        "officeholder": "pareigūnas",
        "council_group": "tarybos frakcija",
        "coalition": "koalicija",
        "not_applicable": "netaikoma",
    },
}


def interpretation_html(answer: dict[str, Any]) -> str:
    value = answer.get("interpretation") or {}
    if not value:
        return '<div class="small mt1">Struktūrizuotos interpretacijos nėra.</div>'
    full = "taip" if value.get("supports_full_proposition") else "ne"
    human = "taip" if value.get("human_approved") else "ne, laukia žmogaus"
    rows = [
        ("Ar citata pagrindžia visą teiginį", full),
        ("Lygmuo", INTERPRETATION_LABELS["government_level"].get(value.get("government_level"), value.get("government_level") or "—")),
        ("Dokumento statusas", INTERPRETATION_LABELS["document_status"].get(value.get("document_status"), value.get("document_status") or "—")),
        ("Kalbėtojo autoritetas", INTERPRETATION_LABELS["speaker_authority"].get(value.get("speaker_authority"), value.get("speaker_authority") or "—")),
        ("Sąlygos", value.get("conditions") or "—"),
        ("Kaina ir kompromisai", value.get("costs_tradeoffs") or "—"),
        ("Ribos", value.get("limitations") or "—"),
        ("Žmogaus patvirtinta", human),
    ]
    return (
        '<div class="small mt1 interpretation">'
        + " · ".join(f"<b>{e(label)}:</b> {e(str(text))}" for label, text in rows)
        + "</div>"
    )


def context_html(answer: dict[str, Any]) -> str:
    context = answer.get("source_context")
    if not context:
        return ""
    quote = answer.get("quote", "")
    safe = e(context)
    if quote:
        safe = safe.replace(e(quote), f"<mark>{e(quote)}</mark>", 1)
    return (
        '<details class="mt1"><summary class="small"><b>Šaltinio kontekstas</b> '
        '(apie 340 simbolių prieš ir po citatos)</summary>'
        f'<div class="context-box">{safe}</div></details>'
    )


def sources_content(
    data: dict[str, Any],
    sources: dict[str, Any],
    manifest: dict[str, Any],
    checks: dict[str, Any],
    mb: dict[str, Any],
    human: dict[str, Any],
) -> str:
    manifest_by_id = {row["source_id"]: row for row in manifest["sources"]}
    source_rows = []
    for source in sources["sources"]:
        row = manifest_by_id.get(source["id"], {})
        source_rows.append(
            f"""
            <tr>
              <td><strong>{e(source['id'])}</strong><br><span class="small">{e(source['kind'])}</span></td>
              <td>{e(source['publisher'])}<br><span class="small">{e(source['published'])}</span></td>
              <td>{e(source['label'])}</td>
              <td>HTTP {e(row.get('http_status', '—'))}<br><span class="hash">{e((row.get('text_sha256') or '')[:16])}</span></td>
              <td>{ext(source['url'], 'Atidaryti')}</td>
            </tr>
            """
        )
    evidence_rows = []
    for party in data["parties"]:
        pid = party["id"]
        for statement in data["statements"]:
            answer = data["answers"][pid][statement["id"]]
            stance = answer.get("stance", "unknown")
            if stance == "unknown":
                quote_cell = (
                    f'<span class="small">{e(answer.get("note", "Nėra cituojamos pozicijos."))}</span>'
                    + interpretation_html(answer)
                )
                source_cell = "—"
                gate = chip("Neįskaityta", "amber")
            else:
                quote_cell = f'<div class="quote">„{e(answer.get("quote", ""))}“</div>'
                source_cell = (
                    ext(answer.get("source_url", ""), answer.get("source_id", "šaltinis"))
                    + f'<div class="small">{e(answer.get("source_date", ""))}</div>'
                )
                if answer.get("scoreable") is True and answer.get("quote_verified") is True:
                    gate = chip("Įskaityta", "green")
                else:
                    gate = chip("Neįskaityta", "amber")
                if answer.get("review_note"):
                    quote_cell += f'<div class="small mt1">{e(answer["review_note"])}</div>'
                quote_cell += context_html(answer)
                quote_cell += interpretation_html(answer)
            evidence_rows.append(
                f"""
                <tr>
                  <td><strong>{e(party['short_name'])}</strong><br><span class="small">{e(statement['topic_id'])}</span></td>
                  <td>{e(statement['text'])}</td>
                  <td>{chip(stance_label(stance), 'green' if stance != 'unknown' else 'gray')}</td>
                  <td>{quote_cell}</td>
                  <td>{source_cell}</td>
                  <td>{gate}</td>
                </tr>
                """
            )
    mb_quotes = "".join(
        f'<li><div class="quote">„{e(item["quote"])}“</div><div class="meta">{ext(mb["methodology"]["url"], "Mano Balsas metodika")}</div></li>'
        for item in mb["methodology"]["quotes"]
    )
    dataset_quotes = sum(
        1 for rows in data["answers"].values() for answer in rows.values() if answer.get("quote")
    )
    scoreable_rows = sum(
        1 for rows in data["answers"].values() for answer in rows.values() if answer.get("scoreable") is True
    )
    unknown_rows = sum(
        1 for rows in data["answers"].values() for answer in rows.values() if answer.get("stance") == "unknown"
    )
    non_scoreable_rows = sum(
        1
        for rows in data["answers"].values()
        for answer in rows.values()
        if answer.get("stance") != "unknown" and answer.get("scoreable") is not True
    )
    mb_quote_count = len(mb["methodology"]["quotes"])
    current_sources = [s for s in sources["sources"] if not s["id"].startswith("manobalsas_")]
    context_sources = [s for s in sources["sources"] if s["id"].startswith("manobalsas_")]
    return f"""
    <section class="hero">
      <span class="eyebrow"><span class="dot"></span>Šaltiniai ir citatų patikra</span>
      <h1>Kiekvienas balas turi pėdsaką.</h1>
      <p class="lead">Šis puslapis rodo, iš ko sudarytas klausimynas: oficialų VRK registrą, 2027 m. partijų dokumentus, galiojančio Rinkimų kodekso ištraukas, kiekvienos citatos šaltinį ir tai, kur pozicijos nėra. Tai techninė citatų ir interpretacijos patikra, ne nepriklausoma teisinė ar politinė peržiūra.</p>
      <div class="banner"><strong>Oficialus 2027 m. biuletenis dar nepaskelbtas.</strong> VRK galutinius kandidatų sąrašus ir numerius numato paskelbti iki {e(data.get('election', {}).get('final_lists_date', '2027-02-05'))}. Šiame puslapyje – tik turimi 2027 m. programiniai dokumentai ir sąrašų kampanijos įrašai.</div>
      <div class="banner green"><strong>{len(sources['sources'])} šaltiniai · {len(checks['checks'])} patikrintos citatos.</strong> {len(current_sources)} – 2027 m. dokumentai ir teisės aktas, {len(context_sources)} – Mano Balsas 2024 kontekstas. Visi citatų įrašai turi būti randami išsaugotame šaltinio tekste. Nepriklausoma žmogaus peržiūra dar neatlikta.</div>
    </section>

    <section class="grid grid-4">
      <div class="card flat"><div class="kicker">Šaltiniai</div><h2>{len(sources['sources'])}</h2><p class="small">{len(current_sources)} 2027 m. dokumentai ir {len(context_sources)} Mano Balsas konteksto šaltiniai.</p></div>
      <div class="card flat"><div class="kicker">Citatos</div><h2>{len(checks['checks'])}</h2><p class="small">{dataset_quotes} dataset eilutės + {mb_quote_count} Mano Balsas metodikos citatos.</p></div>
      <div class="card flat"><div class="kicker">Įskaitoma</div><h2>{scoreable_rows}</h2><p class="small">Tik šios eilutės patenka į balą. {non_scoreable_rows} neįskaitomos, {unknown_rows} nežinomos.</p></div>
      <div class="card flat"><div class="kicker">Žmogaus peržiūra</div><h2>{human['counts']['human_approved_rows']}</h2><p class="small">Nepriklausomai patvirtintų eilučių kol kas nėra.</p></div>
    </section>

    <section class="card mt2">
      <div class="kicker">Registras</div>
      <h2>Dabartiniai šaltiniai</h2>
      <div class="table-wrap">
        <table>
          <caption class="sr-only">2027 m. šaltinių registras su HTTP būsena ir teksto maišos reikšme</caption>
          <thead><tr><th>ID</th><th>Leidėjas / data</th><th>Dokumentas</th><th>HTTP / teksto hash</th><th>Nuoroda</th></tr></thead>
          <tbody>{''.join(source_rows)}</tbody>
        </table>
      </div>
      <p class="small mt2">Kiekvienas šaltinis turi atskirą turinio SHA-256 ir išgauto teksto SHA-256. Patikros data: {e(checks['generated_at'])}.</p>
    </section>

    <section class="card mt2">
      <div class="kicker">Po partiją</div>
      <h2>Visos 2027 m. eilutės</h2>
      <p>Žemiau – kiekvienas klausimas kiekvienai partijai. „Neįskaityta“ nereiškia priešingos pozicijos: tai reiškia, kad trūksta pilnos, partijai priskirtos ir patikrintos citatos.</p>
      <div class="table-wrap">
        <table>
          <caption class="sr-only">Visos 2027 m. partijų pozicijų eilutės</caption>
          <thead><tr><th>Partija</th><th>Teiginys</th><th>Pozicija</th><th>Citata / paaiškinimas</th><th>Šaltinis</th><th>Balas</th></tr></thead>
          <tbody>{''.join(evidence_rows)}</tbody>
        </table>
      </div>
    </section>

    <section class="card mt2">
      <div class="kicker">Interpretacijos ribos</div>
      <h2>Citata ≠ visas pažadas ≠ būsimas sprendimas</h2>
      <p>Kiekviena eilutė turi atskirą interpretacijos įrašą: ar citata pagrindžia visą teiginį, kokios sąlygos, kokia kaina ir kompromisai, ar tai savivaldybės ar nacionalinio lygmens dokumentas, koks dokumento statusas ir koks kalbėtojo autoritetas. Ne visa tai gali būti automatiškai patikrinta.</p>
      <div class="grid grid-2">
        <div>
          <h3>Ką rezultatas matuoja</h3>
          <ul class="list-clean">
            <li>Tavo atsakymų ir cituojamų programinių pozicijų tekstinį sutapimą.</li>
            <li>Temų, kuriose yra įskaitoma pozicija, skaičių ir aprėptį.</li>
            <li>Kur partija neturi pakankamai konkrečios pozicijos – tai rodoma kaip spraga.</li>
          </ul>
        </div>
        <div>
          <h3>Ko rezultatas nematuoja</h3>
          <ul class="list-clean">
            <li>Rinkimų kampanijos poveikio, partijos populiarumo ar galimų mandatų.</li>
            <li>Koalicijų, derybų ar realios savivaldybės daugumos.</li>
            <li>Ar pažadas bus įgyvendintas, per kokį laiką ir už kokią kainą.</li>
            <li>Kandidatų kompetencijos, sąžiningumo ar strateginio balsavimo naudos.</li>
          </ul>
        </div>
      </div>
      <div class="callout warn mt2"><strong>Nenaudok rezultato kaip strateginio balsavimo patarimo.</strong> Jei nori paveikti mandatų pasiskirstymą ar koaliciją, šis įrankis tam netinka – jis lygina tik programų tekstus.</div>
    </section>

    <section class="card mt2">
      <div class="kicker">Kontekstas, ne 2027 m. pozicija</div>
      <h2>Mano Balsas 2024</h2>
      <p>Mano Balsas 2026-10-27 neturėjo 2027 m. Vilniaus savivaldybės testo. Jo 2024 m. nacionalinė metodika ir klausimynas naudojami kaip klausimyno dizaino įkvėpimas, ne kaip partijų vietos pažadai.</p>
      <div class="grid grid-2">
        <div>
          <h3>Ką paėmėme</h3>
          <ul class="list-clean">
            <li>Aiškų „neturiu nuomonės“ atskyrėmą nuo priešingos pozicijos.</li>
            <li>Atskirą temų / dimensijų rodymą.</li>
            <li>Atsakymų sutapimą klausimas po klausimo, o ne vieną nepermatomą reitingą.</li>
          </ul>
          <p class="small mt1">2024 m. teste buvo {mb['test']['question_count']} klausimai, {len(mb['test']['dimensions'])} dalys: {e(', '.join(mb['test']['dimensions']))}.</p>
        </div>
        <div>
          <h3>Ko neperkėlėme</h3>
          <ul class="list-clean">
            <li>2024 m. nacionalinių partijų koordinačių į 2027 m. Vilniaus sąrašus.</li>
            <li>Nacionalinių pažadų kaip savivaldybės įsipareigojimų.</li>
            <li>Jokio Mano Balsas balo į šio klausimyno rezultatą.</li>
          </ul>
          <p class="small mt1">Metodikos citatos:</p>
          <ul class="list-clean">{mb_quotes}</ul>
        </div>
      </div>
    </section>

    <section class="card mt2">
      <div class="kicker">Patikros grandinė</div>
      <h2>Ką reiškia „patikrinta“</h2>
      <ol class="steps">
        <li><b>Šaltinio tinkamumas.</b> Tik HTTPS, 200 atsakymas, 2027 m. kampanijos dokumentas arba aiškiai atskirtas kontekstas.</li>
        <li><b>Išgavimas.</b> HTML ar PDF tekstas išsaugomas su turinio ir teksto SHA-256.</li>
        <li><b>Citatos sutapimas.</b> Normuota citata turi būti randama išgautame tekste; jei ne – eilutė neįskaitoma.</li>
        <li><b>Priskyrimas.</b> Citata turi būti pasakyta pačios partijos arba jos oficialaus kandidato, o ne žurnalisto ar oponento.</li>
        <li><b>Pilnumas.</b> Citata turi pagrįsti visą teiginį, ne tik jo dalį. Dalinė pozicija lieka „neįskaityta“ su paaiškinimu.</li>
        <li><b>Žmogaus peržiūra.</b> Kol kas neatlikta. Tai aiškiai rodoma rezultate ir turi būti atlikta prieš viešą naudojimą.</li>
      </ol>
      <div class="banner mt2"><strong>Ko tai neįrodo:</strong> kad citata teisingai interpretuota, kad partija laikysis pažado, ar kad programa bus įgyvendinta. Tai šaltinio ir citatos patikra, ne politinis vertinimas.</div>
      <div class="callout warn mt2"><strong>Žmogaus peržiūra: {human['status']}.</strong> Paketas turi {len(human['checklist'])} kontrolinius klausimus; {sum(1 for item in human['checklist'] if item['status'] == 'blocked')} jų blokuoti oficialių 2027 m. biuletenio duomenų. {internal('data/human_validation_v21.json', 'Peržiūros paketas →', 'btn btn-soft btn-sm')}</div>
    </section>

    <section class="card mt2">
      <div class="kicker">Duomenų rinkinys</div>
      <h2>Atviri failai</h2>
      <p>Viešai publikuojami tik sutvarkyti duomenys be žalių scraped HTML failų ir be asmeninių darbo failų.</p>
      <div class="inline-links">
        {internal('data/vilnius_2027_v21.json', 'Klausimyno duomenys', 'btn btn-soft btn-sm')}
        {internal('data/vilnius_2027_register.json', 'VRK registro snapshot', 'btn btn-soft btn-sm')}
        {internal('data/source_manifest_v21.json', 'Šaltinių manifestas', 'btn btn-ghost btn-sm')}
        {internal('data/source_checks_v21.json', 'Citatų patikra', 'btn btn-ghost btn-sm')}
        {internal('data/manobalsas_2024_context.json', 'Mano Balsas kontekstas', 'btn btn-ghost btn-sm')}
        {internal('data/human_validation_v21.json', 'Žmogaus peržiūros paketas', 'btn btn-ghost btn-sm')}
        {internal('data/coverage_gaps_v21.json', 'Aprėpties spragos', 'btn btn-ghost btn-sm')}
      </div>
    </section>
    """


def law_article(law: dict[str, Any], number: int) -> dict[str, Any]:
    for article in law.get("articles", []):
        if article.get("article") == number:
            return article
    raise BuildError(f"law article {number} missing")


def law_citation(law: dict[str, Any], number: int, label: str | None = None) -> str:
    article = law_article(law, number)
    title = article.get("title") or ""
    text = f"Rinkimų kodekso {number} straipsnis"
    if title:
        text += f" „{title}“"
    return ext(law["source"]["url"], label or text)


def methodology_content(
    data: dict[str, Any],
    register: dict[str, Any],
    mb: dict[str, Any],
    human: dict[str, Any],
    law: dict[str, Any],
) -> str:
    snapshot = register.get("snapshot", {}).get("date") or register["generated_at"]
    final_date = register.get("election", {}).get("final_lists_date", "2027-02-05")
    preference_votes = register.get("election", {}).get("preference_votes", 5)
    scoreable_stances = [
        answer["stance"]
        for rows in data["answers"].values()
        for answer in rows.values()
        if answer.get("scoreable") is True and answer.get("quote_verified") is True
    ]
    all_agree = bool(scoreable_stances) and all(stance == "agree" for stance in scoreable_stances)
    stance_limit = (
        f'<li><strong>Viena pozicijų kryptis.</strong> Visos {len(scoreable_stances)} įskaitomos 2027 m. programų pozicijos yra „sutinku“; nėra įskaitomos priešingos pozicijos. Todėl rezultatas labiau rodo temų aprėptį negu priešingas stovyklas.</li>'
        if all_agree
        else ""
    )
    art143 = law_article(law, 143)
    return f"""
    <section class="hero">
      <span class="eyebrow"><span class="dot"></span>Metodika · v21</span>
      <h1>Kaip iš atsakymų gaunamas programų atitikimas – ir kodėl jis nėra galutinis.</h1>
      <p class="lead">Metodika sukurta taip, kad nežinoma pozicija neliktų paslėpta, o skirtingas partijų duomenų kiekis nebūtų maskuojamas vienu skaičiumi. Rezultatas yra dalinė, cituojama programų tekstų suvestinė – ne rinkimų prognozė, ne mandatų prognozė ir ne teisinė konsultacija.</p>
      <div class="banner"><strong>Oficialus 2027 m. biuletenis dar nepaskelbtas.</strong> Numatoma, kad galutiniai sąrašai ir numeriai bus paskelbti iki {e(final_date)}. Iki tol klausimynas lygina {len(register['vilnius']['lists'])} sąrašų kampanijos įrašus iš {e(snapshot)} kopijos, o ne galutinį biuletenį.</div>
    </section>

    <section class="grid grid-2">
      <div class="card">
        <div class="kicker">1 žingsnis</div>
        <h2>Kas įskaitoma</h2>
        <ul class="list-clean">
          <li>2027 m. savivaldybės rinkimų programa arba oficialus partijos / kandidato pareiškimas.</li>
          <li>Citata, kurioje partija pati įsipareigoja veiksmui, o ne žurnalistas, oponentas ar 2023 m. programa.</li>
          <li>Pilnas teiginys, atitinkantis klausimą, ne atskiras fragmentas.</li>
          <li>HTTPS šaltinis, sėkmingas 200 atsakymas ir citatos sutapimas su išsaugotu tekstu.</li>
          <li>Struktūrizuota interpretacija: ar citata pagrindžia visą teiginį, sąlygos, kaina, lygmuo, dokumento statusas ir kalbėtojo autoritetas.</li>
        </ul>
      </div>
      <div class="card">
        <div class="kicker">2 žingsnis</div>
        <h2>Kas neįskaitoma</h2>
        <ul class="list-clean">
          <li>Nėra pozicijos → lieka „nežinoma“, ne neigiamas balas.</li>
          <li>Dalinė pozicija → rodoma, bet neįskaitoma, jei nepagrindžia viso teiginio.</li>
          <li>Nacionalinė 2024 m. Mano Balsas pozicija → tik kontekstas, ne 2027 m. Vilniaus pažadas.</li>
          <li>2023 m. programa ar rezultatas → tik istorinis kontekstas.</li>
          <li>Partijai nepriskiriama citata → neįskaitoma net jei tekstas skamba palankiai.</li>
        </ul>
      </div>
    </section>

    <section class="card mt2">
      <div class="kicker">Skaičiavimas</div>
      <h2>Nuo atsakymo iki balo</h2>
      <p>Kiekvienas tavo atsakymas ir partijos pozicija verčiami į 5 balų skalę:</p>
      <div class="table-wrap">
        <table>
          <caption class="sr-only">Atsakymo ir partijos pozicijos balų lentelė</caption>
          <thead><tr><th>Tavo atsakymas</th><th>Reikšmė</th><th>Partijos pozicija</th><th>Reikšmė</th></tr></thead>
          <tbody>
            <tr><td>Visiškai sutinku</td><td>+2</td><td>Visiškai sutinku</td><td>+2</td></tr>
            <tr><td>Greičiau sutinku</td><td>+1</td><td>Greičiau sutinku</td><td>+1</td></tr>
            <tr><td>Nei taip, nei ne</td><td>0</td><td>Nei taip, nei ne</td><td>0</td></tr>
            <tr><td>Greičiau nesutinku</td><td>−1</td><td>Greičiau nesutinku</td><td>−1</td></tr>
            <tr><td>Visiškai nesutinku</td><td>−2</td><td>Visiškai nesutinku</td><td>−2</td></tr>
          </tbody>
        </table>
      </div>
      <div class="callout mt2"><strong>Vieno atsakymo taškai = 100 − 25 × |tavo reikšmė − partijos reikšmė|.</strong> Sutapimas duoda 100, vieno žingsnio skirtumas – 75, dviejų – 50, trijų – 25, keturių – 0.</div>
      <p class="mt2">Tavo pasirinkta svarbiausia tema gauna dvigubą svorį. Partijos balas – svertinis vidurkis tik tų atsakytų klausimų, kuriuose partija turi įskaitomą poziciją. Nežinomi klausimai neįtraukiami, o ne vertinami kaip nesutarimas.</p>
    </section>

    <section class="grid grid-2 mt2">
      <div class="card">
        <div class="kicker">Aprėptis</div>
        <h2>Kodėl rodomas antras skaičius</h2>
        <p><strong>Aprėptis = įskaitomos pozicijos / tavo atsakyti klausimai.</strong> Jei partija turi 5 pozicijas iš 10 atsakytų klausimų, jos aprėptis 50 %, net jei balas 100.</p>
        <ul class="list-clean">
          <li>Bent 35 % atsakytų klausimų turi turėti įskaitomą poziciją.</li>
          <li>Bent 3 žinomos pozicijos arba visi atsakyti klausimai, jei jų mažiau.</li>
          <li>Bent 2 temos, jei pasirinkai 2 ar daugiau temų.</li>
          <li>Bent 2 atsakymai bendrai, kad būtų galima rodyti rezultatą.</li>
        </ul>
      </div>
      <div class="card">
        <div class="kicker">Artumas</div>
        <h2>Kada neskelbiame vieno sąrašo</h2>
        <p>Jei du ar trys sąrašai skiriasi mažiau nei 5 balais arba viršutinio sąrašo aprėptis mažesnė nei 50 %, rezultatas žymimas „keli sąrašai arti“. Tada siūlome rinktis pagal temą, o ne pagal vieną bendrą skaičių.</p>
        <p>Jei geriausias programų atitikimas nesiekia 50/100, sąrašo neišskiriame net tada, kai jis techniškai surenka daugiausia taškų. Tai reiškia, kad turimos pozicijos labiau prieštarauja tavo atsakymams arba jų per mažai prasmingam palyginimui.</p>
        <p>Tai sąmoningas sprendimas: skirtingas duomenų kiekis gali sukurti dirbtinį pranašumą. Geriau parodyti artumą ir spragas nei apsimesti, kad skirtumas tikslus.</p>
      </div>
    </section>

    <section class="card mt2">
      <div class="kicker">Balso planas</div>
      <h2>Ką reiškia rezultatas biuletenyje</h2>
      <ol class="steps">
        <li><b>Tarybos sąrašas.</b> Jei rezultatas „ryškus“, rodome labiausiai pagal programą atitinkantį sąrašą. Jei „arti“ – 2–3 sąrašus ir temų palyginimą.</li>
        <li><b>Pirmumo balsai.</b> {law_citation(law, 143)} numato iki {preference_votes} pirmumo balsų; sąraše su 2–5 kandidatais – mažiau. 2027 m. biuletenio išdėstymas ir žymėjimo pavyzdys dar nepaskelbti, todėl juos patikrink VRK biuletenyje.</li>
        <li><b>Mero biuletenis.</b> Meras renkamas atskirai. Šiame kampanijos registre esantis asmens vardas nepatvirtina jo įtraukimo į mero biuletenį.</li>
        <li><b>Oficialus biuletenis.</b> Galutinis sprendimas visada priklauso nuo VRK biuletenio balsavimo vietoje.</li>
      </ol>
      <div class="banner mt2"><strong>Teisinė išlyga:</strong> šiame puslapyje cituojama šiuo metu galiojanti Rinkimų kodekso redakcija ({law['source']['act_number']}, galioja {law['source']['in_force_from']} – {law['source']['in_force_to']}). Tai ne teisinė konsultacija; oficialų 2027 m. biuletenį ir jo žymėjimą tvirtina VRK.</div>
      <details class="mt2"><summary><b>Rinkimų kodekso ištrauka: 143 straipsnis</b></summary><div class="quote mt1">{e(art143['excerpt'])}</div><p class="small mt1">{ext(law['source']['url'], 'Aktuali Rinkimų kodekso redakcija')}</p></details>
    </section>

    <section class="card mt2" id="balsavimo-galia">
      <div class="kicker">Balsavimo galia</div>
      <h2>Kodėl programų atitikimas nėra „balsavimo galios“ maksimizavimas</h2>
      <p>Programų tekstų sutapimas nematuoja, kiek tavo balsas paveiks mandatų pasiskirstymą, koaliciją ar būsimus sprendimus. Tam reikėtų apklausų, mandatų modelio ir koalicijų scenarijų, kurių šiame įrankyje nėra. Todėl įrankis sąmoningai neteikia strateginio balsavimo patarimo.</p>
      <div class="grid grid-2">
        <div>
          <h3>Nuo balso iki mandato</h3>
          <ul class="list-clean">
            <li>Savivaldybės tarybos sąrašas gali gauti mandatų, jei peržengia 4 % barjerą; jungtinis sąrašas – 6 % ({law_citation(law, 168, '168 straipsnio 4 dalis')}).</li>
            <li>Mandatai paskirstomi kvotų ir liekanų metodu ({law_citation(law, 170, '170 straipsnis')}).</li>
            <li>Pirmumo balsai keičia kandidatų eilę sąraše ({law_citation(law, 171, '171 straipsnis')}).</li>
            <li>Meras renkamas atskirai; prireikus rengiamas pakartotinis balsavimas ({law_citation(law, 167, '167 straipsnio 4 dalis')}).</li>
          </ul>
        </div>
        <div>
          <h3>Ko tam reikėtų, bet neturime</h3>
          <ul class="list-clean">
            <li>Reprezentatyvių Vilniaus rinkėjų apklausų ir jų neapibrėžtumo.</li>
            <li>Mandatų paskirstymo modelio su kandidatų sąrašais ir pirmumo balsais.</li>
            <li>Koalicijų scenarijų ir derybų galios vertinimo.</li>
            <li>Patikimų duomenų apie tai, kaip kandidatai balsuotų taryboje.</li>
          </ul>
        </div>
      </div>
      <div class="callout warn mt2"><strong>Išvada:</strong> šis įrankis padeda palyginti programas, o ne „maksimizuoti balsavimo galią“. Jei strateginis poveikis tau svarbus, reikia atskiro politinio ir rinkimų modelio, kurio čia nėra.</div>
    </section>

    <section class="card mt2">
      <div class="kicker">Mano Balsas</div>
      <h2>Kas paimta iš 2024 m. metodikos</h2>
      <p>Mano Balsas naudoja partijų klausimyną ir ekspertinį kodavimą. 2026-10-27 jo svetainėje nebuvo 2027 m. Vilniaus savivaldybės testo. Todėl 2024 m. nacionalinė medžiaga čia naudojama tik kaip dizaino įkvėpimas:</p>
      <div class="grid grid-2">
        <div>
          <h3>Paimta</h3>
          <ul class="list-clean">
            <li>„Neturiu nuomonės“ atskyrimas nuo priešingos pozicijos.</li>
            <li>Atskiros dimensijos / temos.</li>
            <li>Atsakymų sutapimas klausimas po klausimo.</li>
          </ul>
        </div>
        <div>
          <h3>Nepaimta</h3>
          <ul class="list-clean">
            <li>2024 m. nacionalinės partijų koordinatės.</li>
            <li>Nacionaliniai pažadai kaip savivaldybės įsipareigojimai.</li>
            <li>Mano Balsas balas į šio įrankio rezultatą.</li>
          </ul>
        </div>
      </div>
      <p class="small mt2">2024 m. testas: {mb['test']['question_count']} klausimai, {len(mb['test']['dimensions'])} dalys. Metodikos ir testo šaltiniai pateikti {internal('sources_2027.html', 'šaltinių puslapyje')}.</p>
    </section>

    <section class="card mt2">
      <div class="kicker">Ribos ir peržiūra</div>
      <h2>Ko dar trūksta prieš viešą naudojimą</h2>
      <ul class="list-clean">
        <li><strong>Galutinis biuletenis.</strong> VRK dar nepaskelbė kandidatų sąrašų, pavardžių ir numerių.</li>
        <li><strong>Žmogaus peržiūra.</strong> Visos eilutės – assistant_checked, ne human_approved. Reikia nepriklausomo teisinio ir turinio patikrinimo; būsena: {human['status']}.</li>
        <li><strong>Programų pokyčiai.</strong> Partijos gali keisti dokumentus; kiekvieną atnaujinimą reikia iš naujo patikrinti.</li>
        <li><strong>Nepaskelbtos pozicijos.</strong> Kai kurios partijos neturi 2027 m. savivaldybės programos. Tai rodoma kaip spraga, ne kaip priešinga pozicija.</li>
        <li><strong>Rinkimų poveikis ir mandatai.</strong> Rezultatas nevertina kampanijos poveikio, galimų mandatų, koalicijų ar realios daugumos.</li>
        <li><strong>Įgyvendinimas.</strong> Programos tekstas neįrodo, kad pažadas bus įgyvendintas, per kokį laiką ar už kokią kainą.</li>
        <li><strong>Strateginis balsavimas.</strong> Rezultatas nėra strateginio balsavimo patarimas ir nevertina, kuris balsas labiau paveiktų mandatų pasiskirstymą.</li>
        {stance_limit}
        <li><strong>Reali vartotojų patirtis.</strong> Reikia testuoti su įvairiais rinkėjais, įskaitant mobiliuosius ir mažo skaitmeninio raštingumo naudotojus.</li>
      </ul>
      <div class="banner mt2"><strong>Statusas:</strong> viešai rodyti galima tik su aiškia „laukia žmogaus peržiūros“ žyma. Nenaudoti kaip oficialios partijos, kandidato ar VRK rekomendacijos.</div>
      <p class="small">{len(human['checklist'])} kontrolinių klausimų, {sum(1 for item in human['checklist'] if item['status'] == 'blocked')} blokuoti oficialių biuletenio duomenų. {internal('data/human_validation_v21.json', 'Atidaryti peržiūros paketą →')}</p>
      <div class="inline-links">
        {internal('sources_2027.html', 'Šaltiniai ir citatos', 'btn btn-soft btn-sm')}
        {internal('register_2027.html', 'VRK registras', 'btn btn-ghost btn-sm')}
        {internal('quiz_v21.html', 'Klausimynas', 'btn btn-ghost btn-sm')}
      </div>
    </section>
    """


def ballot_content(data: dict[str, Any], register: dict[str, Any], law: dict[str, Any]) -> str:
    st = register["official_status"]
    snapshot = register.get("snapshot", {}).get("date") or register["generated_at"]
    preference_votes = register.get("election", {}).get("preference_votes", 5)
    mayor_names = [
        row["mayor_candidate"]["name"].title()
        for row in register["vilnius"]["lists"]
        if row.get("mayor_candidate")
    ]
    mayor_list = ", ".join(mayor_names) if mayor_names else "šiuo metu mero kampanijos dalyvių įrašų nėra"
    art143 = law_article(law, 143)
    art171 = law_article(law, 171)
    art168 = law_article(law, 168)
    examples_pending = st.get("ballot_examples_status") != "published"
    return f"""
    <section class="hero">
      <span class="eyebrow"><span class="dot"></span>Balsavimas · 2027-03-07</span>
      <h1>Kaip pažymėti biuletenį, kai jis dar nepaskelbtas?</h1>
      <p class="lead">2027 m. kovo 7 d. balsuosi dėl Vilniaus miesto tarybos ir mero. Tai du atskiri sprendimai: tarybos sąrašas ir mero kandidatas. VRK gali pateikti juos atskiruose biuleteniuose arba skirtingose to paties biuletenio dalyse; oficialus 2027 m. pavyzdys dar {('pažymėtas „Informacija ruošiama“' if examples_pending else 'paskelbtas')}. Šis puslapis paaiškina galiojančios Rinkimų kodekso redakcijos struktūrą.</p>
      <div class="banner"><strong>Oficialūs biuletenių pavyzdžiai dar nepaskelbti.</strong> VRK puslapis {ext(st['ballot_examples_url'], '„Biuletenių pavyzdžiai ir kiti leidiniai“')} {e(snapshot)} rodė „Informacija ruošiama“ (arba dokumentų nerasta). Nenaudok šio puslapio kaip biuletenio kopijos.</div>
    </section>

    <section class="grid grid-2">
      <div class="card">
        <div class="kicker">Tarybos biuletenis</div>
        <h2>Vienas sąrašas + pirmumo balsai</h2>
        <p>Tarybos biuletenyje pasirenki vieną partijos ar komiteto sąrašą. Toje pačioje dalyje gali pareikšti valią dėl kandidatų, įrašydamas jų rinkimų numerius specialiai pirmumo balsams skirtuose laukeliuose.</p>
        <div class="ballot-demo">
          <div class="ballot-paper" aria-label="Iliustracinis tarybos biuletenio pavyzdys, ne oficialus">
            <div class="ballot-title">Iliustracija · ne oficialus biuletenis</div>
            <div class="ballot-row"><span class="ballot-box">☐</span><span>Sąrašas A</span><span class="ballot-prefs"><span class="pref">1</span><span class="pref">2</span><span class="pref">3</span><span class="pref">4</span><span class="pref">5</span></span></div>
            <div class="ballot-row"><span class="ballot-box">☐</span><span>Sąrašas B</span><span class="ballot-prefs"><span class="pref">1</span><span class="pref">2</span><span class="pref">3</span><span class="pref">4</span><span class="pref">5</span></span></div>
            <div class="ballot-row"><span class="ballot-box">☐</span><span>Sąrašas C</span><span class="ballot-prefs"><span class="pref">1</span><span class="pref">2</span><span class="pref">3</span><span class="pref">4</span><span class="pref">5</span></span></div>
            <div class="ballot-label">Skaičiai 1–5 iliustruoja penkis laukelius. Oficialų išdėstymą ir žymėjimą patikrink VRK pavyzdyje.</div>
          </div>
        </div>
        <p class="small mt2">Pagal {law_citation(law, 143, 'Rinkimų kodekso 143 straipsnį')} rinkėjas gali įrašyti iki {preference_votes} pasirinktų kandidatų rinkimų numerius. Jei sąraše yra 2–5 kandidatai, laukelių skaičius mažesnis: 5 kandidatams – iki 4, 4 – iki 3, 3 – iki 2, 2 – iki 1. Laukelių numeriai neteikia skirtingo pirmumo.</p>
      </div>
      <div class="card">
        <div class="kicker">Mero biuletenis</div>
        <h2>Vienas kandidatas</h2>
        <p>Meras renkamas atskirai. Mero biuletenyje žymimas vienas kandidatas, ne partijos sąrašas.</p>
        <div class="ballot-demo">
          <div class="ballot-paper" aria-label="Iliustracinis mero biuletenio pavyzdys, ne oficialus">
            <div class="ballot-title">Iliustracija · ne oficialus biuletenis</div>
            <div class="ballot-row"><span class="ballot-box">☐</span><span>Kandidatas A</span></div>
            <div class="ballot-row"><span class="ballot-box">☐</span><span>Kandidatas B</span></div>
            <div class="ballot-row"><span class="ballot-box">☐</span><span>Kandidatas C</span></div>
            <div class="ballot-label">Pažymėk vieną kandidatą. Pavardės bus oficialiame biuletenyje.</div>
          </div>
        </div>
        <p class="small mt2">{e(snapshot)} VRK registre buvo šie mero kampanijos dalyviai: {e(mayor_list)}. Tai nepatvirtina jų registracijos mero rinkimų kandidatais; galutinis mero biuletenis dar nepaskelbtas.</p>
      </div>
    </section>

    <section class="card mt2">
      <div class="kicker">Žingsnis po žingsnio</div>
      <h2>Ką daryti balsavimo dieną</h2>
      <ol class="steps">
        <li><b>Pasitikrink apylinkę.</b> Vilniaus miesto rinkimų apygarda Nr. 57. Balsavimo vieta priklauso nuo tavo deklaruotos gyvenamosios vietos.</li>
        <li><b>Gauk biuletenį (-ius) tarybai ir merui.</b> Tai du atskiri sprendimai; VRK gali juos pateikti atskiruose biuleteniuose arba skirtingose to paties biuletenio dalyse.</li>
        <li><b>Taryboje pažymėk vieną sąrašą.</b> Sąrašo numeris bus oficialiame biuletenyje, ne šiame puslapyje.</li>
        <li><b>Pirmumo balsus skirk sąmoningai.</b> Įrašyk kandidatų rinkimų numerius tam skirtuose laukeliuose. Jie keičia kandidatų eilę sąraše pagal {law_citation(law, 171, '171 straipsnį')}.</li>
        <li><b>Mero biuletenyje pažymėk vieną kandidatą.</b> Tai atskiras sprendimas nuo tarybos sąrašo.</li>
        <li><b>Laikykis apylinkės komisijos nurodymų.</b> Ji paaiškins, kur dėti biuletenį (-ius) ir ką daryti, jei jį sugadinai.</li>
      </ol>
      <div class="callout warn mt2"><strong>Nedaryk:</strong> nežymėk kelių sąrašų tarybos biuletenyje, nerašyk savo pavardės, nefotografuok pažymėto biuletenio, neimk biuletenio iš apylinkės. Oficialias taisykles visada patikrink VRK informacijoje.</div>
    </section>

    <section class="card mt2">
      <div class="kicker">Kaip skaičiuojami balsai</div>
      <h2>Pirmumo balsai keičia eilę, bet ne sąrašo numerį</h2>
      <div class="grid grid-2">
        <div>
          <h3>Pirmumo balsai</h3>
          <p class="small">Pagal {law_citation(law, 158, '158 straipsnį')} neįskaitomi pirmumo balsai, jei įrašytas nesamas kandidato numeris arba pagal įrašą neįmanoma nustatyti rinkėjo valios. To paties kandidato numeris įskaitomas tik vieną kartą.</p>
          <p class="small">Pagal {law_citation(law, 171, '171 straipsnį')} galutinė kandidatų eilė sąraše nustatoma pagal kandidatų rinkimų reitingą, apskaičiuotą iš pirmumo balsų.</p>
        </div>
        <div>
          <h3>Mandatai</h3>
          <p class="small">Pagal {law_citation(law, 168, '168 straipsnio 4 dalį')} savivaldybių tarybų rinkimuose sąrašas gali gauti mandatų, jei už jį balsavo ne mažiau kaip 4 % rinkimuose dalyvavusių rinkėjų; jungtinis sąrašas – ne mažiau kaip 6 %.</p>
          <p class="small">Mandatai paskirstomi kvotų ir liekanų metodu ({law_citation(law, 170, '170 straipsnis')}). Tai paaiškina, kodėl programų atitikimas negali būti verčiamas į mandatų prognozę.</p>
        </div>
      </div>
      <details class="mt2"><summary><b>Rinkimų kodekso 143 straipsnio ištrauka</b></summary><div class="quote mt1">{e(art143['excerpt'])}</div><p class="small mt1">{ext(law['source']['url'], 'Aktuali Rinkimų kodekso redakcija')} · galioja {e(law['source']['in_force_from'])} – {e(law['source']['in_force_to'])}</p></details>
    </section>

    <section class="grid grid-2 mt2">
      <div class="card">
        <div class="kicker">Kur balsuoti</div>
        <h2>Oficiali informacija</h2>
        <ul class="list-clean">
          <li>{ext(register['election']['official_page'], '2027 m. savivaldybių tarybų ir merų rinkimai')} – VRK bendra informacija.</li>
          <li>{ext('https://www.vrk.lt/lt/2027-m-kovo-7-d-savivaldybiu-tarybu-nariu-ir-meru-rinkimai/balsavimas-savivaldybese', 'Balsavimas savivaldybėse')} – VRK balsavimo tvarka.</li>
          <li>{ext(st['ballot_examples_url'], 'Biuletenių pavyzdžiai')} – kol kas „Informacija ruošiama“.</li>
          <li>{ext('https://www.vrk.lt/lt/2027-m-kovo-7-d-savivaldybiu-tarybu-nariu-ir-meru-rinkimai/mokomoji-medziaga', 'Mokomoji medžiaga')} – VRK paaiškinimai.</li>
          <li>{ext(law['source']['url'], 'Rinkimų kodeksas')} – galiojanti redakcija Teisės aktų registre.</li>
        </ul>
      </div>
      <div class="card">
        <div class="kicker">Teisinė išlyga</div>
        <h2>Ko šis puslapis negali</h2>
        <ul class="list-clean">
          <li>Pakeisti oficialaus VRK biuletenio.</li>
          <li>Patvirtinti 2027 m. biuletenio išdėstymo, kol VRK jo nepaskelbė.</li>
          <li>Pasakyti, kas bus galutiniame sąraše ar koks bus sąrašo numeris.</li>
          <li>Būti teisinė konsultacija konkrečiu ginču ar pažeidimu.</li>
        </ul>
        <p class="small mt2">Balsavimo tvarka gali keistis. Prieš rinkimus patikrink VRK puslapį ir savo apylinkės pranešimą.</p>
      </div>
    </section>

    <section class="card mt2">
      <div class="kicker">Kaip tai siejasi su klausimynu</div>
      <h2>Rezultatas padeda pasirinkti, bet nepažymi biuletenio</h2>
      <p>Klausimynas parodo, kurie sąrašai labiausiai atitinka tavo prioritetus pagal turimas cituojamas programų pozicijas. Jis neperkelia atsakymų į biuletenį ir negarantuoja, kad partija įgyvendins pažadus.</p>
      <p>Jei rezultatas „keli sąrašai arti“, rinkis pagal temą, kuri tau svarbiausia, ir pagal konkrečius kandidatus. Jei rezultatas „nepakanka duomenų“, geriau remkis oficialia programa ir kandidatų sąrašu, o ne šiuo įrankiu.</p>
      <div class="inline-links">{internal('quiz_v21.html', 'Grįžti į klausimyną', 'btn btn-primary btn-sm')} {internal('methodology_2027.html', 'Metodika', 'btn btn-soft btn-sm')} {internal('register_2027.html', 'Registras', 'btn btn-ghost btn-sm')}</div>
    </section>
    """


LT_MONTHS = (
    "sausio",
    "vasario",
    "kovo",
    "balandžio",
    "gegužės",
    "birželio",
    "liepos",
    "rugpjūčio",
    "rugsėjo",
    "spalio",
    "lapkričio",
    "gruodžio",
)


def lt_date(value: str) -> str:
    """Format an ISO date for Lithuanian prose without locale dependencies."""
    try:
        year, month, day = (int(part) for part in str(value).split("-"))
    except (TypeError, ValueError):
        return str(value)
    if not 1 <= month <= 12:
        return str(value)
    return f"{year} m. {LT_MONTHS[month - 1]} {day} d."


def topic_map(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {topic["id"]: topic for topic in data["topics"]}


def review_payload(
    data: dict[str, Any],
    register: dict[str, Any],
    gaps: dict[str, Any],
    human: dict[str, Any],
) -> dict[str, Any]:
    topics = topic_map(data)
    gap_by = {
        (gap["party_id"], gap["statement_id"]): gap for gap in gaps.get("gaps", [])
    }
    rows: list[dict[str, Any]] = []
    non_reviewable: list[dict[str, Any]] = []
    for party in data["parties"]:
        for statement in data["statements"]:
            answer = data["answers"][party["id"]][statement["id"]]
            topic = topics.get(statement["topic_id"], {})
            if not answer.get("quote"):
                non_reviewable.append(
                    {
                        "row_id": f'{party["id"]}__{statement["id"]}',
                        "party": party["short_name"],
                        "statement": statement["text"],
                        "topic": topic.get("name", statement["topic_id"]),
                        "stance": answer.get("stance", "unknown"),
                        "note": answer.get("note", ""),
                    }
                )
                continue
            gap = gap_by.get((party["id"], statement["id"]), {})
            rows.append(
                {
                    "row_id": f'{party["id"]}__{statement["id"]}',
                    "party_id": party["id"],
                    "party": party["short_name"],
                    "statement_id": statement["id"],
                    "topic_id": statement["topic_id"],
                    "topic": topic.get("name", statement["topic_id"]),
                    "statement": statement["text"],
                    "stance": answer.get("stance", "unknown"),
                    "label": stance_label(answer.get("stance", "unknown")),
                    "scoreable": bool(answer.get("scoreable") is True),
                    "quote": answer.get("quote", ""),
                    "source_id": answer.get("source_id", ""),
                    "source_url": answer.get("source_url", ""),
                    "source_date": answer.get("source_date", ""),
                    "context": answer.get("source_context", ""),
                    "note": answer.get("review_note") or answer.get("note") or "",
                    "gap_reason": gap.get("reason", ""),
                    "interpretation": answer.get("interpretation") or {},
                }
            )
    return {
        "format": "vaa-human-review-workbench-v1",
        "export_format": human.get("workbench", {}).get(
            "export_format", "vaa-human-review-draft-v1"
        ),
        "generated_at": data["generated_at"],
        "snapshot_date": register.get("snapshot", {}).get("date")
        or register["generated_at"],
        "rows": rows,
        "non_reviewable": non_reviewable,
        "disclaimer": (
            "Tai peržiūros juodraštis, ne patvirtinimas. Eksportas niekada "
            "nenustato human_approved, signoff ar public_use_allowed. "
            "Pavadintas žmogus turi pats peržiūrėti šaltinius ir atnaujinti "
            "vaa/review/human_validation_v21.json."
        ),
    }


def review_card_html(row: dict[str, Any]) -> str:
    status_cls = "green" if row["scoreable"] else "amber"
    status = "Įskaityta" if row["scoreable"] else "Neįskaityta"
    row_id = row["row_id"]
    search = " ".join(
        [
            row["party"],
            row["statement"],
            row["quote"],
            row["context"],
            row["source_id"],
            row["statement_id"],
        ]
    ).lower()
    interpretation = interpretation_html({"interpretation": row["interpretation"]})
    context = context_html(
        {"source_context": row["context"], "quote": row["quote"]}
    )
    gap = (
        f'<div class="small mt1"><b>Kodėl neįskaityta:</b> {e(row["gap_reason"])}</div>'
        if row.get("gap_reason")
        else ""
    )
    return f"""
    <article class="review-card" data-row="{e(row_id)}" data-party="{e(row['party_id'])}" data-statement="{e(row['statement_id'])}" data-search="{e(search)}">
      <div class="review-head">
        <div>{chip(row['party'], 'green')} {chip(row['topic'], 'gray')}</div>
        <span class="small">{e(row['source_id'])} · {e(row['source_date'])}</span>
      </div>
      <p class="small mt1"><b>Teiginys:</b> {e(row['statement'])}</p>
      <div class="quote">„{e(row['quote'])}“</div>
      {context}
      <details class="mt1"><summary class="small"><b>Interpretacijos įrašas</b></summary>{interpretation}</details>
      {gap}
      <fieldset class="decision-row">
        <legend class="sr-only">Sprendimas dėl {e(row['party'])} teiginio „{e(row['statement'])}“</legend>
        <label><input type="radio" name="decision-{e(row_id)}" value="approve"> Patvirtinu</label>
        <label><input type="radio" name="decision-{e(row_id)}" value="reject"> Atmetu</label>
        <label><input type="radio" name="decision-{e(row_id)}" value="needs_more_evidence"> Reikia įrodymų</label>
        <label><input type="radio" name="decision-{e(row_id)}" value="unclear"> Neaišku</label>
      </fieldset>
      <div class="field">
        <label for="note-{e(row_id)}">Peržiūrėtojo pastaba</label>
        <textarea id="note-{e(row_id)}" data-note rows="2" placeholder="Ką patikrinote ir kodėl taip nusprendėte"></textarea>
      </div>
      <div class="meta">{ext(row['source_url'], 'Atidaryti šaltinį')} · {chip(status, status_cls)}</div>
    </article>
    """


def review_content(
    data: dict[str, Any],
    register: dict[str, Any],
    gaps: dict[str, Any],
    human: dict[str, Any],
) -> str:
    payload = review_payload(data, register, gaps, human)
    expected = human.get("workbench", {}).get("reviewable_rows")
    if expected is not None and len(payload["rows"]) != expected:
        raise BuildError(
            f"review workbench has {len(payload['rows'])} rows, expected {expected}"
        )
    party_options = "".join(
        f'<option value="{e(party["id"])}">{e(party["short_name"])}</option>'
        for party in data["parties"]
    )
    cards = "".join(review_card_html(row) for row in payload["rows"])
    non_rows = "".join(
        f'<tr><td><strong>{e(row["party"])}</strong></td><td>{e(row["topic"])}</td><td>{e(row["statement"])}</td><td>{e(row["note"])}</td></tr>'
        for row in payload["non_reviewable"]
    )
    reviewable = len(payload["rows"])
    non_reviewable = len(payload["non_reviewable"])
    return f"""
    <section class="hero">
      <span class="eyebrow"><span class="dot"></span>Žmogaus peržiūra · juodraštis</span>
      <h1>Peržiūros darbo stalas</h1>
      <p class="lead">Čia gali peržiūrėti visas {reviewable} cituojamas 2027 m. eilutes: atidaryti šaltinį, perskaityti kontekstą, palyginti su teiginiu ir pažymėti savo sprendimą. Įrankis tik paruošia juodraštį. Jis niekada nepatvirtina eilutės už žmogų.</p>
      {pending_banner(register)}
      <div class="banner red"><strong>Tai ne patvirtinimas.</strong> Eksportuotas failas turi <code>draft: true</code>, <code>human_approved: false</code> ir <code>signoff: null</code>. Tik pavadintas peržiūrėtojas gali atnaujinti <code>vaa/review/human_validation_v21.json</code> ir pasirašyti.</div>
    </section>

    <section class="card">
      <div class="kicker">Kaip naudotis</div>
      <h2>Keturi veiksmai vienai eilutei</h2>
      <ol class="steps">
        <li><b>Atidaryk šaltinį.</b> Spausk „Atidaryti šaltinį“ ir perskaityk ne tik citatą, bet ir aplinkinį tekstą.</li>
        <li><b>Patikrink kontekstą.</b> Atidenk „Šaltinio kontekstas“ ir įsitikink, kad citata neapversta ar neišplėšta iš sąlygos.</li>
        <li><b>Palygink su teiginiu.</b> Ar citata pagrindžia visą teiginį, o ne vien jo dalį? Ar tai savivaldybės, o ne nacionalinio lygmens įsipareigojimas?</li>
        <li><b>Pažymėk sprendimą ir parašyk pastabą.</b> Sprendimas be pastabos sunkiai patikrinamas. Eksportas lieka juodraščiu.</li>
      </ol>
      <div class="note mt2"><strong>Nepriklausomumas.</strong> Peržiūrėtojas neturi būti tas pats žmogus, kuris kodavo eilutę. AI agentas negali pasirašyti nei eilutės, nei teisinės, nei prieinamumo, nei naudotojų testavimo išvados.</div>
    </section>

    <section class="card" id="review-app">
      <div class="kicker">Darbo stalas</div>
      <h2>{reviewable} peržiūrimos eilutės</h2>
      <div class="tool-bar">
        <div class="field">
          <label for="reviewer-name">Peržiūrėjusio žmogaus vardas</label>
          <input type="text" id="reviewer-name" autocomplete="off" spellcheck="false" placeholder="Vardas Pavardė">
        </div>
        <div class="field">
          <label for="filter-party">Sąrašas</label>
          <select id="filter-party"><option value="all">Visi sąrašai</option>{party_options}</select>
        </div>
        <div class="field">
          <label for="filter-decision">Sprendimas</label>
          <select id="filter-decision">
            <option value="all">Visi</option>
            <option value="undecided">Be sprendimo</option>
            <option value="decided">Turi sprendimą</option>
            <option value="approve">Patvirtinta</option>
            <option value="reject">Atmesta</option>
            <option value="needs_more_evidence">Reikia įrodymų</option>
            <option value="unclear">Neaišku</option>
          </select>
        </div>
        <div class="field">
          <label for="filter-search">Paieška</label>
          <input type="text" id="filter-search" autocomplete="off" spellcheck="false" placeholder="Partija, teiginys, citata">
        </div>
      </div>
      <p class="progress" id="review-progress" role="status" aria-live="polite">0 / {reviewable} eilučių turi sprendimą</p>
      <div class="note" id="review-empty" hidden>Pagal pasirinktus filtrus eilučių nėra.</div>
      <div class="review-list">{cards}</div>
      <div class="inline-links">
        <button class="btn btn-primary btn-sm" type="button" id="export-draft">Atsisiųsti juodraštį</button>
        <button class="btn btn-soft btn-sm" type="button" id="copy-draft">Kopijuoti juodraštį</button>
      </div>
      <p class="small mt1" id="draft-status" role="status" aria-live="polite"></p>
      <div class="field mt2">
        <label for="draft-output">Eksporto peržiūra</label>
        <textarea id="draft-output" class="export-out" readonly aria-describedby="draft-status"></textarea>
      </div>
      <p class="small mt1">Failo formatas <code>{e(payload['export_format'])}</code>. Jame yra visi {reviewable} eilučių įrašai, net tie, kurių dar neperžiūrėjai, kad darbą būtų galima tęsti.</p>
    </section>

    <section class="card mt2">
      <div class="kicker">Kas lieka už darbo stalo</div>
      <h2>{non_reviewable} kombinacijos be cituojamos pozicijos</h2>
      <p>Šios kombinacijos neturi cituojamos pozicijos, todėl jų negalima peržiūrėti kaip citatos. Jos lieka „nėra pozicijos“ ir niekada nevertinamos kaip priešingos. Žemiau – priežastys, kad spragos nebūtų paslėptos.</p>
      <details><summary><b>Rodyti {non_reviewable} kombinacijas</b></summary>
        <div class="table-wrap mt1">
          <table>
            <caption class="sr-only">Kombinacijos be cituojamos pozicijos</caption>
            <thead><tr><th>Sąrašas</th><th>Tema</th><th>Teiginys</th><th>Kodėl nėra citatos</th></tr></thead>
            <tbody>{non_rows}</tbody>
          </table>
        </div>
      </details>
    </section>

    <section class="card mt2">
      <div class="kicker">Toliau</div>
      <h2>Ką daryti su juodraščiu</h2>
      <p>Peržiūrėjęs eilutes, atsisiųsk juodraštį ir perduok jį atsakingam žmogui. Jis turi patikrinti kiekvieną sprendimą, įrašyti savo vardą, datą ir priežastį į peržiūros paketą. Tik tada galima keisti <code>human_approved</code> ir <code>signoff</code>.</p>
      <div class="inline-links">
        {internal('sources_2027.html', 'Visi šaltiniai ir citatos', 'btn btn-soft btn-sm')}
        {internal('methodology_2027.html', 'Metodika', 'btn btn-ghost btn-sm')}
        {internal('data/human_validation_v21.json', 'Peržiūros paketas', 'btn btn-ghost btn-sm')}
      </div>
    </section>
    <script id="review-data" type="application/json">{json_script(payload)}</script>
    """


def compare_payload(
    data: dict[str, Any],
    register: dict[str, Any],
    gaps: dict[str, Any],
) -> dict[str, Any]:
    topics = topic_map(data)
    gap_by = {
        (gap["party_id"], gap["statement_id"]): gap for gap in gaps.get("gaps", [])
    }
    lists = []
    for party in data["parties"]:
        register_row = next(
            (
                row
                for row in register.get("vilnius", {}).get("lists", [])
                if row.get("party_id") == party["id"]
            ),
            {},
        )
        lists.append(
            {
                "id": party["id"],
                "name": party["short_name"],
                "full_name": party["name"],
                "mayor": (register_row.get("mayor_candidate") or {}).get("name") or "",
            }
        )
    statements = [
        {
            "id": statement["id"],
            "topic": topics.get(statement["topic_id"], {}).get(
                "name", statement["topic_id"]
            ),
            "text": statement["text"],
        }
        for statement in data["statements"]
    ]
    cells: dict[str, dict[str, Any]] = {}
    for party in data["parties"]:
        cells[party["id"]] = {}
        for statement in data["statements"]:
            answer = data["answers"][party["id"]][statement["id"]]
            gap = gap_by.get((party["id"], statement["id"]), {})
            stance = answer.get("stance", "unknown")
            scoreable = answer.get("scoreable") is True
            if scoreable:
                label = stance_label(stance)
            elif stance == "unknown":
                label = "Nėra pozicijos"
            else:
                label = "Neįskaityta"
            interpretation = answer.get("interpretation") or {}
            cells[party["id"]][statement["id"]] = {
                "stance": stance,
                "label": label,
                "scoreable": scoreable,
                "quote": answer.get("quote", ""),
                "source_id": answer.get("source_id", ""),
                "source_url": answer.get("source_url", ""),
                "source_date": answer.get("source_date", ""),
                "context": answer.get("source_context", ""),
                "note": answer.get("review_note") or answer.get("note") or "",
                "gap_reason": gap.get("reason", ""),
                "supports_full": bool(interpretation.get("supports_full_proposition")),
                "document_status": interpretation.get("document_status", ""),
            }
    return {
        "format": "vaa-compare-workbench-v1",
        "generated_at": data["generated_at"],
        "lists": lists,
        "statements": statements,
        "cells": cells,
    }


def compare_cell_html(cell: dict[str, Any]) -> str:
    if cell["scoreable"]:
        status_cls = "green"
    elif cell["stance"] == "unknown":
        status_cls = "gray"
    else:
        status_cls = "amber"
    parts = [chip(cell["label"], status_cls)]
    if cell.get("quote"):
        parts.append(f'<div class="quote mt1">„{e(cell["quote"])}“</div>')
    elif cell.get("note"):
        parts.append(f'<div class="small mt1">{e(cell["note"])}</div>')
    if cell.get("context"):
        parts.append(
            '<details class="mt1"><summary class="small">Šaltinio kontekstas</summary>'
            f'<div class="context-box">{e(cell["context"])}</div></details>'
        )
    if cell.get("source_url"):
        parts.append(
            f'<div class="meta mt1">{ext(cell["source_url"], cell.get("source_id") or "šaltinis")}'
            f' · {e(cell.get("source_date", ""))}</div>'
        )
    if cell.get("gap_reason"):
        parts.append(
            f'<div class="small mt1"><b>Kodėl neįskaityta:</b> {e(cell["gap_reason"])}</div>'
        )
    if cell.get("quote") and cell.get("supports_full") is False:
        parts.append(
            '<div class="small mt1">Interpretacija: citata nepagrindžia viso teiginio.</div>'
        )
    return "".join(parts)


def compare_table_html(
    payload: dict[str, Any], left: str, right: str
) -> str:
    left_name = next(
        (row["name"] for row in payload["lists"] if row["id"] == left), left
    )
    right_name = next(
        (row["name"] for row in payload["lists"] if row["id"] == right), right
    )
    rows = []
    for statement in payload["statements"]:
        rows.append(
            f"""
            <tr>
              <td><strong>{e(statement['text'])}</strong><div class="meta">{e(statement['topic'])}</div></td>
              <td data-head="{e(left_name)}">{compare_cell_html(payload['cells'][left][statement['id']])}</td>
              <td data-head="{e(right_name)}">{compare_cell_html(payload['cells'][right][statement['id']])}</td>
            </tr>
            """
        )
    return "".join(rows)


def compare_content(
    data: dict[str, Any],
    register: dict[str, Any],
    gaps: dict[str, Any],
) -> str:
    payload = compare_payload(data, register, gaps)
    left = payload["lists"][0]["id"]
    right = payload["lists"][-1]["id"]

    def options_for(selected: str) -> str:
        return "".join(
            f'<option value="{e(row["id"])}"'
            + (' selected' if row["id"] == selected else "")
            + f'>{e(row["name"])} – {e(row["full_name"])}</option>'
            for row in payload["lists"]
        )

    options_a = options_for(left)
    options_b = options_for(right)
    counts = {}
    for row in payload["lists"]:
        values = payload["cells"][row["id"]].values()
        counts[row["id"]] = {
            "scoreable": sum(1 for cell in values if cell["scoreable"]),
            "unknown": sum(1 for cell in values if cell["stance"] == "unknown"),
            "non_scoreable": sum(
                1
                for cell in values
                if cell["stance"] != "unknown" and not cell["scoreable"]
            ),
        }
    summary_rows = "".join(
        f'<li><b>{e(row["name"])}</b>: {counts[row["id"]]["scoreable"]} įskaitomos, '
        f'{counts[row["id"]]["non_scoreable"]} neįskaitomos, '
        f'{counts[row["id"]]["unknown"]} be pozicijos.</li>'
        for row in payload["lists"]
    )
    return f"""
    <section class="hero">
      <span class="eyebrow"><span class="dot"></span>Palyginimas · du sąrašai</span>
      <h1>Palygink du sąrašus pagal tuos pačius teiginius</h1>
      <p class="lead">Pasirink du Vilniaus miesto sąrašus ir pamatyk, kur jie turi tą pačią cituojamą poziciją, kur pozicija neįskaitoma ir kur jos visai nėra. Tai programų tekstų palyginimas, ne rekomendacija ir ne prognozė.</p>
      {pending_banner(register)}
      <div class="banner"><strong>Trūkstama pozicija nėra „prieš“.</strong> „Nėra pozicijos“ reiškia, kad 2027 m. dokumente neradome tinkamos citatos. Tokia eilutė negauna neigiamo balo ir nėra laikoma priešinga pozicija.</div>
    </section>

    <section class="card">
      <div class="kicker">Aprėptis</div>
      <h2>Ką kiekvienas sąrašas turi</h2>
      <ul class="list-clean">{summary_rows}</ul>
      <p class="small mt1">Skaičiai susiję su {len(payload['statements'])} klausimyno teiginių. Visi įskaitomi įrašai šiame duomenų rinkinyje yra „sutinku“ krypties; priešingų įskaitomų pozicijų kol kas nėra.</p>
    </section>

    <section class="card mt2" id="compare-app">
      <div class="kicker">Palyginimo lentelė</div>
      <h2>Teiginys po teiginio</h2>
      <div class="compare-controls">
        <div class="field">
          <label for="list-a">Sąrašas A</label>
          <select id="list-a">{options_a}</select>
        </div>
        <div class="field">
          <label for="list-b">Sąrašas B</label>
          <select id="list-b">{options_b}</select>
        </div>
        <button class="btn btn-soft btn-sm" type="button" id="swap-lists">Sukeisti A ir B</button>
      </div>
      <p class="small mt1" id="compare-warning" hidden>Pasirinkai tą patį sąrašą abiejose pusėse. Pakeisk vieną, kad palyginimas būtų prasmingas.</p>
      <p class="small mt1" id="compare-summary" role="status" aria-live="polite"></p>
      <div class="table-wrap mt2">
        <table class="compare-table">
          <caption class="sr-only">Dviejų sąrašų palyginimas pagal klausimyno teiginius</caption>
          <thead><tr><th>Teiginys</th><th id="compare-head-a">{e(payload['lists'][0]['name'])}</th><th id="compare-head-b">{e(payload['lists'][-1]['name'])}</th></tr></thead>
          <tbody id="compare-body">{compare_table_html(payload, left, right)}</tbody>
        </table>
      </div>
      <div class="tag-row mt2">{chip('Įskaityta', 'green')} {chip('Neįskaityta', 'amber')} {chip('Nėra pozicijos', 'gray')}</div>
      <div class="share-field">
        <label class="lbl" for="share-url">Nuoroda į šį palyginimą</label>
        <input type="text" id="share-url" readonly value="compare_2027.html#a={e(left)}&amp;b={e(right)}">
      </div>
      <p class="small mt1">Pasirinkimas įrašomas į adreso fragmentą (<code>#a=…&amp;b=…</code>). Jis nepalieka šios svetainės ir nenaudoja slapukų.</p>
    </section>

    <section class="card mt2">
      <div class="kicker">Kaip skaityti</div>
      <h2>Trys būsenos, ne du „už“ ir „prieš“</h2>
      <div class="grid grid-3">
        <div><h3>Įskaityta</h3><p class="small">Yra tiksli citata, ji priskirta sąrašui ir pagrindžia visą teiginį. Tik ši būsena patenka į klausimyno balą.</p></div>
        <div><h3>Neįskaityta</h3><p class="small">Citata yra, bet ji pagrindžia tik dalį teiginio, yra ne savivaldybės lygmens arba dokumentas netinkamas. Priežastis rodoma lentelėje.</p></div>
        <div><h3>Nėra pozicijos</h3><p class="small">2027 m. dokumente neradome tinkamos citatos. Tai spraga, ne priešingas pažadas ir ne neigiamas balas.</p></div>
      </div>
      <div class="callout warn mt2"><strong>Nelygink sąrašų vien pagal įskaitomų eilučių skaičių.</strong> Didesnis skaičius reiškia daugiau rastos cituojamos medžiagos, o ne geresnę programą ar didesnę politinę įtaką.</div>
      <div class="inline-links">
        {internal('sources_2027.html', 'Šaltiniai ir citatos', 'btn btn-soft btn-sm')}
        {internal('quiz_v21.html', 'Klausimynas', 'btn btn-ghost btn-sm')}
        {internal('methodology_2027.html', 'Metodika', 'btn btn-ghost btn-sm')}
      </div>
    </section>
    <script id="compare-data" type="application/json">{json_script(payload)}</script>
    """


def voter_content(
    data: dict[str, Any], register: dict[str, Any], law: dict[str, Any]
) -> str:
    st = register["official_status"]
    snapshot = register.get("snapshot", {}).get("date") or register["generated_at"]
    election_date = register["election"]["date"]
    final_date = st["candidate_lists_published_by"]["date"]
    preference_votes = register.get("election", {}).get("preference_votes", 5)
    election_day = date.fromisoformat(election_date)
    residence_deadline = election_day - timedelta(days=60)
    home_request_deadline = election_day - timedelta(days=1)
    while home_request_deadline.weekday() != 2:
        home_request_deadline -= timedelta(days=1)
    events = [
        (
            residence_deadline.isoformat(),
            "Paskutinė diena deklaruoti gyvenamąją vietą",
            "Skaičiuojama pagal Rinkimų kodekso 8 straipsnio 3 dalį: ne vėliau kaip likus 60 dienų iki rinkimų dienos. Data apskaičiuota nuo 2027-03-07.",
        ),
        (
            "2027-02-04",
            "VRK sprendimai dėl kandidatų sąrašų",
            "Paskutinė diena, kai VRK priima sprendimą registruoti kandidatą, kandidatų sąrašą arba jų neregistruoti.",
        ),
        (
            final_date,
            "VRK paskelbia galutinius sąrašus ir numerius",
            "Pagal Rinkimų kodekso 84 straipsnį VRK paskelbia kandidatų sąrašus, kandidatus, jų numerius ir pareiškinius dokumentus.",
        ),
        (
            "2027-02-27",
            "Baigiamas pranešimų apie rinkimus įteikimas",
            "Kartu su pranešimu rinkėjui įteikiamas leidinys su kandidatų sąrašų programomis.",
        ),
        (
            "2027-02-28",
            "Galutiniai rinkėjų sąrašai",
            "Išankstiniai rinkėjų sąrašai su pripažintais pakeitimais tvirtinami kaip galutiniai.",
        ),
        (
            "2027-03-02",
            "Prasideda balsavimas iki rinkimų dienos",
            "Balsuoti galima savivaldybės pastate ar kitose suderintose patalpose iki kovo 4 d., 7–20 val.",
        ),
        (
            home_request_deadline.isoformat(),
            "Paskutinė diena prašyti balsuoti namuose",
            "Pagal Rinkimų kodekso 138 straipsnio 2 dalį prašymai priimami iki paskutinio trečiadienio iki rinkimų dienos.",
        ),
        (
            "2027-03-05",
            "Balsavimas namuose",
            "Namuose balsuojama kovo 5–6 d., 8–20 val. Tik tiems rinkėjams, kurie turi teisę ir yra įrašyti į namuose balsuojančių rinkėjų sąrašą.",
        ),
        (
            election_date,
            "Rinkimų diena",
            "Balsavimas apylinkėse vyksta 7–20 val. Tai dviejų sprendimų diena: tarybos sąrašas ir mero kandidatas.",
        ),
        (
            "2027-03-21",
            "Antrasis mero rinkimų turas, jei reikia",
            "Jei per pirmą balsavimą nė vienas kandidatas nesurenka reikiamos daugumos, po dviejų savaičių rengiamas pakartotinis balsavimas dėl dviejų daugiausia balsų gavusių kandidatų.",
        ),
    ]
    first_future = next(
        (index for index, event in enumerate(events) if event[0] >= snapshot), None
    )
    timeline = []
    for index, (event_date, title, text) in enumerate(events):
        cls = "done" if event_date < snapshot else ("next" if index == first_future else "future")
        timeline.append(
            f'<li class="{cls}"><b>{e(lt_date(event_date))} · {e(title)}</b><span>{e(text)}</span></li>'
        )
    art8 = law_article(law, 8)
    art9 = law_article(law, 9)
    art16 = law_article(law, 16)
    art62 = law_article(law, 62)
    art84 = law_article(law, 84)
    art131 = law_article(law, 131)
    art132 = law_article(law, 132)
    art134 = law_article(law, 134)
    art135 = law_article(law, 135)
    art138 = law_article(law, 138)
    art139 = law_article(law, 139)
    art142 = law_article(law, 142)
    art143 = law_article(law, 143)
    art158 = law_article(law, 158)
    art167 = law_article(law, 167)
    art168 = law_article(law, 168)
    art171 = law_article(law, 171)
    calendar = ext(CALENDAR_URL, "VRK 2027 m. rinkimų grafikas")
    return f"""
    <section class="hero">
      <span class="eyebrow"><span class="dot"></span>Rinkėjui · {e(election_date)}</span>
      <h1>Ką daryti prieš balsuojant ir balsavimo dieną</h1>
      <p class="lead">Praktinis Vilniaus rinkėjo vadovas: kas gali balsuoti, kokie terminai, kaip balsuoti iš anksto, namuose ar savo apylinkėje, kokių dokumentų reikia ir kaip elgtis, jei reikia pagalbos. Datos remiasi oficialiu {calendar} ir galiojančiu Rinkimų kodeksu.</p>
      {pending_banner(register)}
      <div class="banner"><strong>Rinkėjai iš anksto neregistruojami.</strong> Politinės kampanijos dalyvių registracija baigėsi {e(lt_date('2026-10-28'))}, bet tai ne rinkėjo registracija. Rinkėjas įrašomas į apylinkės rinkėjų sąrašą pagal deklaruotą gyvenamąją vietą. Atskirai prašyti reikia tik balsavimo namuose.</div>
    </section>

    <section class="grid grid-4">
      <div class="card flat"><div class="kicker">Rinkimų diena</div><h2>{e(lt_date(election_date))}</h2><p class="small">Balsavimas apylinkėse 7–20 val. ({law_citation(law, 139, 'Rinkimų kodekso 139 str. 2 d.')}).</p></div>
      <div class="card flat"><div class="kicker">Iš anksto</div><h2>Kovo 2–4 d.</h2><p class="small">7–20 val. savivaldybės pastate ar kitose suderintose patalpose ({law_citation(law, 134, '134 str. 3 d.')}).</p></div>
      <div class="card flat"><div class="kicker">Namuose</div><h2>Kovo 5–6 d.</h2><p class="small">8–20 val. Tik turintiems teisę ir pateikus prašymą iki kovo 3 d. ({law_citation(law, 138, '138 str.')}).</p></div>
      <div class="card flat"><div class="kicker">Antras turas</div><h2>Kovo 21 d.</h2><p class="small">Mero rinkimuose, jei pirmame ture nė vienas negauna reikiamos daugumos ({law_citation(law, 167, '167 str. 4 d.')}).</p></div>
    </section>

    <section class="card mt2">
      <div class="kicker">Teisė balsuoti</div>
      <h2>Kas gali balsuoti Vilniaus savivaldybės rinkimuose?</h2>
      <div class="grid grid-2">
        <div>
          <h3>Amžius ir gyvenamoji vieta</h3>
          <p>Pagal {law_citation(law, 8, 'Rinkimų kodekso 8 straipsnio 2 dalį')} teisę rinkti savivaldybės tarybos narius ir merą turi nuolatiniai šios savivaldybės gyventojai, kuriems rinkimų dieną yra sukakę 18 metų.</p>
          <p>Pagal to paties straipsnio 3 dalį nuolatiniu gyventoju laikomas Lietuvos pilietis, kitos ES valstybės narės pilietis, turintis teisę gyventi Lietuvoje, arba kitas asmuo, turintis teisę nuolat gyventi Lietuvoje ir tai patvirtinantį dokumentą, jeigu jie ne vėliau kaip likus 60 dienų iki rinkimų dienos yra deklaravę gyvenamąją vietą šioje savivaldybėje arba įtraukti į nedeklaravusių asmenų apskaitą.</p>
          <div class="note mt1"><strong>Data:</strong> 60 dienų iki 2027 m. kovo 7 d. yra <b>{e(lt_date(residence_deadline.isoformat()))}</b>. Tai apskaičiuota data, ne atskiras VRK grafikos punktas.</div>
        </div>
        <div>
          <h3>Apribojimai</h3>
          <p>Pagal {law_citation(law, 9, 'Rinkimų kodekso 9 straipsnį')} teisės rinkti neturi asmuo, teismo pripažintas neveiksniu su rinkimų teisės įgyvendinimu susijusiose srityse.</p>
          <p>Tai visuotinė rinkimų teisė: nereikia specialios registracijos partijoje, nereikia mokėti mokesčio ir nereikia išankstinės registracijos balsuoti. Pakanka būti įrašytam į savo apylinkės rinkėjų sąrašą.</p>
          <h3 class="mt2">Dėl ko pasitikrinti</h3>
          <ul class="list-clean">
            <li>Ar tavo deklaruota gyvenamoji vieta yra Vilniaus mieste?</li>
            <li>Ar rinkimų dieną tau jau sukaks 18 metų?</li>
            <li>Ar esi teismo pripažintas neveiksniu su rinkimų teise susijusioje srityje?</li>
          </ul>
        </div>
      </div>
      <p class="small mt2">Savivaldybės taryba renkama daugiamandatėje apygardoje, meras – vienmandatėje; abi apygardos sutampa su savivaldybės teritorija ({law_citation(law, 16, 'Rinkimų kodekso 16 straipsnio 4 dalis')}). Vilniaus miesto apygarda – Nr. {e(register['election']['apygarda'])}.</p>
    </section>

    <section class="card mt2">
      <div class="kicker">Terminai</div>
      <h2>Ką reikia padaryti iki rinkimų</h2>
      <ul class="timeline">{''.join(timeline)}</ul>
      <p class="small mt2">Visos datos, išskyrus gyvenamosios vietos deklaravimo ir prašymo balsuoti namuose datas, paimtos iš {calendar}. Pastarosios dvi apskaičiuotos pagal Rinkimų kodekso 8 ir 138 straipsnius. Prieš rinkimus patikrink VRK puslapį: tvarka gali būti tikslinama.</p>
    </section>

    <section class="card mt2">
      <div class="kicker">Balsavimo būdai</div>
      <h2>Keturi keliai iki biuletenio</h2>
      <div class="grid grid-2">
        <div>
          <h3>1. Rinkimų dieną savo apylinkėje</h3>
          <p>Atvyk į savo apylinkės balsavimo patalpas {e(lt_date(election_date))} nuo 7 iki 20 val. ({law_citation(law, 139, '139 str. 2 d.')}). Balsavimo vieta priklauso nuo tavo deklaruotos gyvenamosios vietos; ją rasi VRK informacijoje arba pranešime apie rinkimus.</p>
          <p>Pateik asmens dokumentą, gauk biuletenį (-ius), pažymėk tarybos sąrašą ir mero kandidatą. Tai du atskiri sprendimai.</p>
        </div>
        <div>
          <h3>2. Iš anksto</h3>
          <p>Balsavimas iki rinkimų dienos vyksta kovo 2–4 d. nuo 7 iki 20 val. savivaldybės pastate ar (ir) kitose balsavimui tinkamose ir su savivaldybės administracija suderintose patalpose ({law_citation(law, 134, '134 str. 3 d.')}).</p>
          <p>Išankstinis balsavimas patogus, jei rinkimų dieną nebūsi savo apylinkėje. Balsuoti galima tik vieną kartą: jei jau balsavai iš anksto, rinkimų dieną antrą kartą balsuoti negalima.</p>
        </div>
        <div>
          <h3>3. Namuose</h3>
          <p>Balsuoti namuose gali neįgalieji, neįgaliuosius slaugantys ar prižiūrintys asmenys, dėl ligos laikinai nedarbingi rinkėjai ir 70 metų bei vyresni rinkėjai, jeigu dėl sveikatos būklės patys negali atvykti į apylinkę, yra pateikę VRK formos prašymą ir įrašyti į namuose balsuojančių rinkėjų sąrašą ({law_citation(law, 138, '138 str. 1 d.')}).</p>
          <p>Prašymai priimami iki paskutinio trečiadienio iki rinkimų dienos – <b>{e(lt_date(home_request_deadline.isoformat()))}</b> ({law_citation(law, 138, '138 str. 2 d.')}). Balsuojama kovo 5–6 d. 8–20 val. ({law_citation(law, 134, '134 str. 5 d.')}).</p>
        </div>
        <div>
          <h3>4. Kitoje savo apygardos apylinkėje</h3>
          <p>Rinkimų dieną, jei dar nebalsavai, gali balsuoti kitoje savo apygardos apylinkėje, jeigu abi apylinkės prisijungusios prie elektroninio rinkėjų sąrašo ir komisijos gali patvirtinti, kad nebalsavai nė vienoje apylinkėje ({law_citation(law, 62, 'Rinkimų kodekso 62 straipsnis')}).</p>
          <p>Tai ne leidimas balsuoti bet kurioje Lietuvos apylinkėje: apylinkė turi būti toje pačioje rinkimų apygardoje.</p>
        </div>
      </div>
    </section>

    <section class="grid grid-2 mt2">
      <div class="card">
        <div class="kicker">Dokumentai</div>
        <h2>Ką būtina turėti</h2>
        <p>Balsuoti atvykęs rinkėjas pateikia vieną galiojantį dokumentą ({law_citation(law, 142, 'Rinkimų kodekso 142 straipsnio 1 dalis')}):</p>
        <ul class="list-clean">
          <li>asmens tapatybės kortelę;</li>
          <li>pasą;</li>
          <li>vairuotojo pažymėjimą;</li>
          <li>kitą valstybės institucijos ar jos įgaliotos įstaigos išduotą dokumentą, pagal kurį galima nustatyti asmens tapatybę.</li>
        </ul>
        <div class="callout warn mt2"><strong>Jei dokumento neturi ar jis pasibaigęs,</strong> kreipkis į VRK arba savo apylinkės rinkimų komisiją prieš rinkimų dieną. Neatidėk to paskutinei minutei.</div>
      </div>
      <div class="card">
        <div class="kicker">Pagalba ir prieinamumas</div>
        <h2>Jei pats negaliu užpildyti biuletenio</h2>
        <p>Negalią turinčiam rinkėjui turi būti sudarytos sąlygos balsuojant naudotis pagalbinėmis priemonėmis ir technologijomis. Jei dėl neįgalumo rinkėjas pats negali atlikti balsavimo veiksmų, jo prašymu juos atlieka rinkėjo pasirinktas asmuo. Šis asmuo privalo užpildyti biuletenį rinkėjo akivaizdoje pagal jo nurodymą ir saugoti balsavimo paslaptį ({law_citation(law, 135, '135 str. 6 d.')}).</p>
        <p>Balsavimo patalpos turi būti įvertintos ir pritaikytos neįgalių bei senyvo amžiaus rinkėjų poreikiams ({law_citation(law, 131, '131 str. 2 d.')}), o tam numatytos savivaldybės biudžeto lėšos ({law_citation(law, 132, '132 str. 5 d.')}).</p>
        <p class="small">Jei reikia pagalbos ar pritaikytos patalpos, susisiek su savivaldybės rinkimų komisija arba VRK iš anksto, kad spėtum pasiruošti.</p>
      </div>
    </section>

    <section class="card mt2">
      <div class="kicker">Antrasis turas</div>
      <h2>Meras gali būti renkamas dar kartą</h2>
      <p>Mero rinkimuose kandidatas laikomas išrinktu pirmą kartą balsuojant, jei gavo daugiau kaip pusę rinkimuose dalyvavusių rinkėjų balsų ir rinkimuose dalyvavo ne mažiau kaip 40 % į apygardos rinkėjų sąrašus įrašytų rinkėjų. Jei dalyvavo mažiau kaip 40 %, išrinktu laikomas daugiausia, bet ne mažiau kaip 1/5 visų įrašytų rinkėjų balsų gavęs kandidatas ({law_citation(law, 167, '167 str. 4 d.')}).</p>
      <p>Jei per pirmą balsavimą nė vienas kandidatas nesurenka reikiamos daugumos, ne vėliau kaip po dviejų savaičių rengiamas pakartotinis balsavimas dėl dviejų daugiausia balsų gavusių kandidatų ({law_citation(law, 167, '167 str. 4 d. 3 p.')}). Pagal {calendar} antrasis turas numatytas <b>{e(lt_date('2027-03-21'))}</b>: balsavimas iki rinkimų dienos kovo 16–18 d. 7–20 val., balsavimas namuose kovo 19–20 d. 8–20 val.</p>
      <p class="small">Antrasis turas vyksta tik mero rinkimuose. Tarybos sudėtis nustatoma pagal pirmojo balsavimo rezultatus.</p>
    </section>

    <section class="card mt2">
      <div class="kicker">Dažnos klaidos</div>
      <h2>Ko nedaryti</h2>
      <div class="grid grid-2">
        <div>
          <ul class="list-clean">
            <li>Nežymėk kelių tarybos sąrašų viename biuletenyje.</li>
            <li>Neįrašyk daugiau pirmumo balsų numerių, nei leidžia sąrašo dydis.</li>
            <li>Nefotografuok pažymėto biuletenio ir neimk jo iš apylinkės.</li>
            <li>Nebalsuok du kartus – nei iš anksto, nei skirtingose apylinkėse.</li>
          </ul>
        </div>
        <div>
          <ul class="list-clean">
            <li>Nepamiršk, kad tarybos sąrašas ir mero kandidatas yra du atskiri sprendimai.</li>
            <li>Nepainiok partijos sąrašo numerio su kandidato rinkimų numeriu.</li>
            <li>Nesivadovauk šiuo puslapiu kaip oficialiu biuleteniu – jo dar nėra.</li>
            <li>Jei sugadinai biuletenį, kreipkis į apylinkės komisiją; ji paaiškins tvarką.</li>
          </ul>
        </div>
      </div>
      <div class="callout warn mt2"><strong>Pirmumo balsai.</strong> Pagal {law_citation(law, 143, '143 straipsnį')} gali įrašyti iki {e(str(preference_votes))} pasirinktų kandidatų numerių, o jei sąraše mažiau kandidatų – mažiau. Pagal {law_citation(law, 158, '158 straipsnį')} neįskaitomi pirmumo balsai, jei numeris nesamas arba pagal įrašą neįmanoma nustatyti valios. Pagal {law_citation(law, 171, '171 straipsnį')} pirmumo balsai keičia kandidatų eilę sąraše.</div>
    </section>

    <section class="card mt2">
      <div class="kicker">Kur patikrinti</div>
      <h2>Oficialūs šaltiniai</h2>
      <ul class="list-clean">
        <li>{ext(register['election']['official_page'], 'VRK 2027 m. savivaldybių tarybų ir merų rinkimai')} – bendra informacija, apylinkės, balsavimo tvarka.</li>
        <li>{ext(CALENDAR_URL, 'VRK 2027 m. rinkimų grafikas')} – oficialios datos ir terminai.</li>
        <li>{ext(law['source']['url'], 'Rinkimų kodeksas')} – galiojanti redakcija Teisės aktų registre ({e(law['source']['in_force_from'])} – {e(law['source']['in_force_to'])}).</li>
        <li>{ext(st['ballot_examples_url'], 'Biuletenių pavyzdžiai')} – kol kas „Informacija ruošiama“.</li>
        <li>{ext('https://www.vrk.lt/lt/2027-m-kovo-7-d-savivaldybiu-tarybu-nariu-ir-meru-rinkimai/mokomoji-medziaga', 'VRK mokomoji medžiaga')} – paaiškinimai rinkėjams.</li>
      </ul>
      <div class="banner mt2"><strong>Tai ne teisinė konsultacija.</strong> Puslapis paaiškina bendrą tvarką pagal {e(snapshot)} galiojusią Rinkimų kodekso redakciją ir VRK grafiką. Konkrečiu atveju vadovaukis oficialiu biuleteniu, apylinkės komisijos nurodymais ir VRK informacija.</div>
      <div class="inline-links">
        {internal('ballot_2027.html', 'Kaip pažymėti biuletenį', 'btn btn-primary btn-sm')}
        {internal('register_2027.html', 'Sąrašų registras', 'btn btn-soft btn-sm')}
        {internal('sources_2027.html', 'Šaltiniai', 'btn btn-ghost btn-sm')}
      </div>
    </section>
    """


REVIEW_SCRIPT = """
<script>
(function(){
  "use strict";
  var app = document.getElementById("review-app");
  if (!app) { return; }
  var payload = JSON.parse(document.getElementById("review-data").textContent);
  var cards = Array.prototype.slice.call(app.querySelectorAll(".review-card"));
  var partyFilter = document.getElementById("filter-party");
  var decisionFilter = document.getElementById("filter-decision");
  var searchFilter = document.getElementById("filter-search");
  var progress = document.getElementById("review-progress");
  var reviewer = document.getElementById("reviewer-name");
  var output = document.getElementById("draft-output");
  var status = document.getElementById("draft-status");
  var empty = document.getElementById("review-empty");
  var known = ["approve","reject","needs_more_evidence","unclear"];

  function currentDecision(card){
    var checked = card.querySelector('input[type="radio"]:checked');
    return checked ? checked.value : "";
  }
  function refresh(){
    var query = (searchFilter.value || "").trim().toLowerCase();
    var party = partyFilter.value;
    var wanted = decisionFilter.value;
    var decided = 0;
    var shown = 0;
    cards.forEach(function(card){
      var decision = currentDecision(card);
      if (decision) { decided += 1; }
      var visible = true;
      if (party !== "all" && card.getAttribute("data-party") !== party) { visible = false; }
      if (query && (card.getAttribute("data-search") || "").indexOf(query) === -1) { visible = false; }
      if (wanted === "undecided" && decision) { visible = false; }
      if (wanted === "decided" && !decision) { visible = false; }
      if (known.indexOf(wanted) !== -1 && decision !== wanted) { visible = false; }
      card.hidden = !visible;
      if (visible) { shown += 1; }
    });
    progress.textContent = decided + " / " + cards.length + " eilučių turi sprendimą";
    empty.hidden = shown !== 0;
  }
  function collect(){
    var rows = cards.map(function(card){
      var note = card.querySelector("textarea[data-note]");
      return {
        row_id: card.getAttribute("data-row"),
        party_id: card.getAttribute("data-party"),
        statement_id: card.getAttribute("data-statement"),
        decision: currentDecision(card),
        note: note ? note.value : ""
      };
    });
    var decided = rows.filter(function(row){ return row.decision; }).length;
    return {
      format: payload.export_format,
      draft: true,
      human_approved: false,
      signoff: null,
      generated_at: new Date().toISOString(),
      dataset_generated_at: payload.generated_at,
      reviewer: (reviewer.value || "").trim(),
      counts: {
        reviewable: rows.length,
        decided: decided,
        undecided: rows.length - decided
      },
      decisions: rows,
      disclaimer: payload.disclaimer
    };
  }
  function prepare(){
    var draft = collect();
    var text = JSON.stringify(draft, null, 2);
    output.value = text;
    if (!draft.reviewer) {
      status.textContent = "Juodraštis paruoštas be peržiūrėjusio vardo. Įrašyk jį prieš perduodamas failą.";
    } else {
      status.textContent = "Juodraštis paruoštas. Jis nepatvirtina nė vienos eilutės ir nekeičia signoff.";
    }
    return text;
  }
  function updatePreview(){
    output.value = JSON.stringify(collect(), null, 2);
  }
  document.getElementById("export-draft").addEventListener("click", function(){
    var text = prepare();
    var blob = new Blob([text], {type:"application/json"});
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = "vaa-human-review-draft.json";
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(function(){ URL.revokeObjectURL(url); }, 1500);
  });
  document.getElementById("copy-draft").addEventListener("click", function(){
    var text = prepare();
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function(){
        status.textContent = "Juodraštis nukopijuotas. Jis vis dar nėra patvirtinimas.";
      }).catch(function(){
        output.focus();
        output.select();
        status.textContent = "Automatinis kopijavimas nepavyko. Tekstas pažymėtas – nukopijuok rankiniu būdu.";
      });
    } else {
      output.focus();
      output.select();
      status.textContent = "Automatinis kopijavimas nepalaikomas. Tekstas pažymėtas – nukopijuok rankiniu būdu.";
    }
  });
  app.addEventListener("change", function(event){
    if (event.target && event.target.type === "radio") { refresh(); updatePreview(); }
  });
  app.addEventListener("input", function(event){
    if (!event.target) { return; }
    if (event.target.getAttribute("data-note") !== null || event.target === reviewer) {
      updatePreview();
    }
  });
  [partyFilter, decisionFilter, searchFilter].forEach(function(control){
    control.addEventListener("input", refresh);
    control.addEventListener("change", refresh);
  });
  updatePreview();
  refresh();
})();
</script>
"""


COMPARE_SCRIPT = """
<script>
(function(){
  "use strict";
  var app = document.getElementById("compare-app");
  if (!app) { return; }
  var payload = JSON.parse(document.getElementById("compare-data").textContent);
  var selectA = document.getElementById("list-a");
  var selectB = document.getElementById("list-b");
  var body = document.getElementById("compare-body");
  var headA = document.getElementById("compare-head-a");
  var headB = document.getElementById("compare-head-b");
  var share = document.getElementById("share-url");
  var summary = document.getElementById("compare-summary");
  var warning = document.getElementById("compare-warning");

  function listById(id){
    for (var index = 0; index < payload.lists.length; index += 1) {
      if (payload.lists[index].id === id) { return payload.lists[index]; }
    }
    return payload.lists[0];
  }
  function el(tag, cls, text){
    var node = document.createElement(tag);
    if (cls) { node.className = cls; }
    if (text !== undefined && text !== null) { node.textContent = text; }
    return node;
  }
  function cellNode(cell){
    var wrap = el("div");
    var chipClass = cell.scoreable ? "green" : (cell.stance === "unknown" ? "gray" : "amber");
    wrap.appendChild(el("span", "chip " + chipClass, cell.label));
    if (cell.quote) {
      wrap.appendChild(el("blockquote", "quote mt1", "„" + cell.quote + "“"));
    } else if (cell.note) {
      wrap.appendChild(el("p", "small mt1", cell.note));
    }
    if (cell.context) {
      var details = el("details", "mt1");
      details.appendChild(el("summary", "small", "Šaltinio kontekstas"));
      details.appendChild(el("div", "context-box", cell.context));
      wrap.appendChild(details);
    }
    if (cell.source_url) {
      var link = el("a", "small", cell.source_id || "šaltinis");
      link.href = cell.source_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      wrap.appendChild(link);
      if (cell.source_date) { wrap.appendChild(el("div", "meta", cell.source_date)); }
    }
    if (cell.gap_reason) {
      wrap.appendChild(el("div", "small mt1", "Kodėl neįskaityta: " + cell.gap_reason));
    }
    if (cell.quote && cell.supports_full === false) {
      wrap.appendChild(el("div", "small mt1", "Interpretacija: citata nepagrindžia viso teiginio."));
    }
    return wrap;
  }
  function render(){
    var left = selectA.value;
    var right = selectB.value;
    headA.textContent = listById(left).name;
    headB.textContent = listById(right).name;
    body.textContent = "";
    var both = 0;
    var one = 0;
    var none = 0;
    payload.statements.forEach(function(statement){
      var cellA = payload.cells[left][statement.id];
      var cellB = payload.cells[right][statement.id];
      if (cellA.scoreable && cellB.scoreable) { both += 1; }
      else if (cellA.scoreable || cellB.scoreable) { one += 1; }
      else { none += 1; }
      var row = document.createElement("tr");
      var statementCell = document.createElement("td");
      statementCell.appendChild(el("strong", "", statement.text));
      statementCell.appendChild(el("div", "meta", statement.topic));
      row.appendChild(statementCell);
      var cellNodeA = document.createElement("td");
      cellNodeA.setAttribute("data-head", listById(left).name);
      cellNodeA.appendChild(cellNode(cellA));
      row.appendChild(cellNodeA);
      var cellNodeB = document.createElement("td");
      cellNodeB.setAttribute("data-head", listById(right).name);
      cellNodeB.appendChild(cellNode(cellB));
      row.appendChild(cellNodeB);
      body.appendChild(row);
    });
    summary.textContent = both + " teiginiai turi įskaitomą poziciją abiejuose sąrašuose; " + one + " – tik viename; " + none + " – nė viename.";
    warning.hidden = left !== right;
    var fragment = "#a=" + encodeURIComponent(left) + "&b=" + encodeURIComponent(right);
    share.value = window.location.href.split("#")[0] + fragment;
    try {
      window.history.replaceState(null, "", fragment);
    } catch (error) {
      /* Some file:// contexts refuse replaceState; the table still works. */
    }
  }
  function initial(){
    var params = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    var left = params.get("a");
    var right = params.get("b");
    if (left && payload.cells[left]) { selectA.value = left; }
    if (right && payload.cells[right]) { selectB.value = right; }
  }
  selectA.addEventListener("change", render);
  selectB.addEventListener("change", render);
  document.getElementById("swap-lists").addEventListener("click", function(){
    var left = selectA.value;
    selectA.value = selectB.value;
    selectB.value = left;
    render();
  });
  share.addEventListener("focus", function(){ share.select(); });
  initial();
  render();
})();
</script>
"""


def build_quiz(
    data: dict[str, Any],
    register: dict[str, Any],
    manifest: dict[str, Any],
    checks: dict[str, Any],
    mb: dict[str, Any],
) -> str:
    template = QUIZ_TEMPLATE.read_text("utf-8")
    engine = ENGINE.read_text("utf-8")
    meta = make_meta(data, register, manifest, checks, mb)
    synced = sync_party_mayors(data, register)
    quiz = template.replace("/*__DATA__*/", json_script(synced))
    quiz = quiz.replace("/*__REGISTER__*/", json_script(trimmed_register(register)))
    quiz = quiz.replace("/*__META__*/", json_script(meta))
    quiz = quiz.replace("/*__ENGINE__*/", engine)
    if "/*__" in quiz:
        raise BuildError("unreplaced placeholder in quiz template")
    return quiz


def static_page(
    title: str,
    description: str,
    content: str,
    active: str,
    generated: str,
    script: str = "",
    csp: str = DEFAULT_CSP,
) -> str:
    template = STATIC_TEMPLATE.read_text("utf-8")
    nav = {
        "quiz": "__NAV_QUIZ__",
        "register": "__NAV_REGISTER__",
        "sources": "__NAV_SOURCES__",
        "methodology": "__NAV_METHOD__",
        "ballot": "__NAV_BALLOT__",
        "voter": "__NAV_VOTER__",
    }
    for key, token in nav.items():
        template = template.replace(token, ' aria-current="page"' if key == active else "")
    page = (
        template.replace("__CSP__", csp)
        .replace("__TITLE__", e(title))
        .replace("__DESCRIPTION__", e(description))
        .replace("__CONTENT__", content)
        .replace("__GENERATED__", e(generated))
        .replace("__SCRIPT__", script)
    )
    for token in (
        "__CSP__",
        "__TITLE__",
        "__DESCRIPTION__",
        "__CONTENT__",
        "__GENERATED__",
        "__SCRIPT__",
        "__NAV_QUIZ__",
        "__NAV_REGISTER__",
        "__NAV_SOURCES__",
        "__NAV_METHOD__",
        "__NAV_BALLOT__",
        "__NAV_VOTER__",
    ):
        if token in page:
            raise BuildError(f"unreplaced placeholder {token} in static template")
    return page


def write_pages(pages: dict[str, str], target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for name, content in pages.items():
        (target / name).write_text(content, encoding="utf-8")


def copy_data(target: Path) -> None:
    data_dir = target / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    for name in DATA_FILES:
        if name == "vilnius_2027_register.json":
            doc = public_register(
                json.loads((ROOT / "vaa" / name).read_text("utf-8"))
            )
            (data_dir / name).write_text(
                json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        else:
            shutil.copy2(ROOT / "vaa" / name, data_dir / name)
    for name in REVIEW_FILES:
        shutil.copy2(ROOT / "vaa" / "review" / name, data_dir / name)
    (target / ".nojekyll").write_text("", encoding="utf-8")
    (target / "robots.txt").write_text(
        "User-agent: *\n"
        "Allow: /\n"
        "\n"
        "# Vilnius 2027 civic guide: static, no tracking, no personalisation.\n",
        encoding="utf-8",
    )


def build() -> dict[str, str]:
    data, register, sources, manifest, checks, mb, law = load_all()
    human = load_human()
    gaps = json.loads(GAPS.read_text("utf-8"))
    if gaps.get("format") != "vaa-coverage-gaps-v21":
        raise BuildError("coverage gaps format invalid")
    snapshot = register.get("snapshot", {}).get("date") or register["generated_at"]
    pages = {
        "index.html": static_page(
            "Rinkimų įrankis",
            "Vilniaus 2027 m. tarybos ir mero rinkimų įrankis: kampanijos registro įrašai, cituojamos pozicijos ir balsavimo planas.",
            index_content(data, register, sources, checks),
            "index",
            data["generated_at"],
        ),
        "quiz_v21.html": build_quiz(data, register, manifest, checks, mb),
        "register_2027.html": static_page(
            "VRK registras",
            f"Oficialus VRK 2027 m. Vilniaus miesto sąrašų registras {snapshot} būsena.",
            register_content(data, register, checks),
            "register",
            data["generated_at"],
        ),
        "sources_2027.html": static_page(
            "Šaltiniai",
            "2027 m. Vilniaus partijų pozicijų šaltiniai, citatų patikra, interpretacijos ribos ir Mano Balsas kontekstas.",
            sources_content(data, sources, manifest, checks, mb, human),
            "sources",
            data["generated_at"],
        ),
        "methodology_2027.html": static_page(
            "Metodika",
            "Kaip v21 klausimynas skaičiuoja programų atitikimą, aprėptį ir balsavimo planą.",
            methodology_content(data, register, mb, human, law),
            "methodology",
            data["generated_at"],
        ),
        "ballot_2027.html": static_page(
            "Balsavimas",
            "2027 m. kovo 7 d. Vilniaus tarybos ir mero biuletenių struktūra, pirmumo balsai pagal Rinkimų kodeksą ir oficialūs VRK šaltiniai.",
            ballot_content(data, register, law),
            "ballot",
            data["generated_at"],
        ),
        "rinkejui_2027.html": static_page(
            "Rinkėjui",
            "Praktinis Vilniaus rinkėjo vadovas: teisė balsuoti, terminai, balsavimas iš anksto ir namuose, dokumentai, pagalba ir antrasis turas.",
            voter_content(data, register, law),
            "voter",
            data["generated_at"],
        ),
        "compare_2027.html": static_page(
            "Palyginti sąrašus",
            "Dviejų Vilniaus 2027 m. sąrašų palyginimas pagal tuos pačius programų teiginius, citatas ir aprėpties spragas.",
            compare_content(data, register, gaps),
            "compare",
            data["generated_at"],
            script=COMPARE_SCRIPT,
            csp=SCRIPT_CSP,
        ),
        "review_2027.html": static_page(
            "Peržiūros darbo stalas",
            "Žmogaus peržiūros darbo stalas 32 cituojamoms 2027 m. eilutėms: šaltinio kontekstas, sprendimai ir juodraščio eksportas.",
            review_content(data, register, gaps, human),
            "review",
            data["generated_at"],
            script=REVIEW_SCRIPT,
            csp=SCRIPT_CSP,
        ),
    }
    write_pages(pages, ROOT)
    if DIST.exists():
        shutil.rmtree(DIST)
    write_pages(pages, DIST)
    copy_data(DIST)
    # Keep curated data next to the root pages too, so local previews work.
    copy_data(ROOT)
    return pages


def check() -> None:
    required = list(PAGES) + [
        "data/vilnius_2027_v21.json",
        "data/vilnius_2027_register.json",
        "data/source_manifest_v21.json",
        "data/source_checks_v21.json",
        "data/manobalsas_2024_context.json",
        "data/human_validation_v21.json",
        "data/coverage_gaps_v21.json",
        "data/vrk_2027_manifest.json",
        "data/sources_2027_v21.json",
        ".nojekyll",
        "robots.txt",
    ]
    for base in (ROOT, DIST):
        for name in required:
            path = base / name
            if not path.exists():
                raise BuildError(f"missing build output: {path.relative_to(ROOT)}")
            if path.stat().st_size < 200 and name not in (".nojekyll", "robots.txt"):
                raise BuildError(f"suspiciously small output: {path.relative_to(ROOT)}")
    for name in PAGES:
        text = (DIST / name).read_text("utf-8")
        for token in (
            "__CSP__",
            "__TITLE__",
            "__DESCRIPTION__",
            "__CONTENT__",
            "__GENERATED__",
            "__SCRIPT__",
            "__NAV_QUIZ__",
            "__NAV_REGISTER__",
            "__NAV_SOURCES__",
            "__NAV_METHOD__",
            "__NAV_BALLOT__",
            "__NAV_VOTER__",
        ):
            if token in text:
                raise BuildError(f"placeholder {token} left in {name}")
        for marker in ("fixtures/", ".cache/", "/Users/", "palace.yaml", "docs/superpowers"):
            if marker in text:
                raise BuildError(f"private path {marker} leaked into {name}")
    for bad_dir in (".cache", "fixtures", "docs", ".claude", ".freebuff"):
        if (DIST / bad_dir).exists():
            raise BuildError(f"private directory copied into dist: {bad_dir}")
    for path in sorted((DIST / "data").glob("*.json")):
        text = path.read_text("utf-8")
        for marker in ("fixtures/", ".cache/", "/Users/", "palace.yaml", ".claude", ".freebuff", "docs/superpowers"):
            if marker in text:
                raise BuildError(f"private path {marker} leaked into data/{path.name}")
    quiz = (DIST / "quiz_v21.html").read_text("utf-8")
    for marker in ("V21Engine", "selected_topics", "app-data", "register-data", "meta-data"):
        if marker not in quiz:
            raise BuildError(f"quiz missing {marker}")
    if "/*__" in quiz:
        raise BuildError("unreplaced placeholder in quiz template")
    for name in (
        "index.html",
        "register_2027.html",
        "sources_2027.html",
        "methodology_2027.html",
        "ballot_2027.html",
        "rinkejui_2027.html",
        "compare_2027.html",
        "review_2027.html",
    ):
        text = (DIST / name).read_text("utf-8")
        if "Oficialus 2027 m. biuletenis dar nepaskelbtas" not in text and name != "ballot_2027.html":
            raise BuildError(f"{name}: missing status disclosure")
    for name in ("compare_2027.html", "review_2027.html"):
        text = (DIST / name).read_text("utf-8")
        if SCRIPT_CSP not in text:
            raise BuildError(f"{name}: missing script CSP")
        if "id=\"review-app\"" not in text and "id=\"compare-app\"" not in text:
            raise BuildError(f"{name}: missing app root")
    review = (DIST / "review_2027.html").read_text("utf-8")
    for marker in ("vaa-human-review-draft-v1", "human_approved", "signoff", "review-data"):
        if marker not in review:
            raise BuildError(f"review page missing {marker}")
    compare = (DIST / "compare_2027.html").read_text("utf-8")
    for marker in ("compare-data", "compare-table", "share-url"):
        if marker not in compare:
            raise BuildError(f"compare page missing {marker}")
    voter = (DIST / "rinkejui_2027.html").read_text("utf-8")
    for marker in ("Rinkimų kodekso 8", "Rinkimų kodekso 138", "2027 m. kovo 2 d.", "2027 m. kovo 21 d."):
        if marker not in voter:
            raise BuildError(f"voter page missing {marker}")
    print(f"OK: {len(PAGES)} pages, clean dist/, {len(required)} checked files")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify generated files")
    parser.add_argument("--no-dist", action="store_true", help="only write root pages")
    args = parser.parse_args()
    try:
        pages = build()
        if args.check:
            check()
        print("built:", ", ".join(sorted(pages)))
    except (BuildError, VaaError, OSError) as error:
        print("ERROR:", error, file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
