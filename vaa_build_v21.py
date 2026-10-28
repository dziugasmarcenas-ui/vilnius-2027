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
DIST = ROOT / "dist"

PAGES = {
    "index.html": "index",
    "quiz_v21.html": "quiz",
    "register_2027.html": "register",
    "sources_2027.html": "sources",
    "methodology_2027.html": "methodology",
    "ballot_2027.html": "ballot",
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
        "source": register["source"],
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


def static_page(title: str, description: str, content: str, active: str, generated: str) -> str:
    template = STATIC_TEMPLATE.read_text("utf-8")
    nav = {
        "quiz": "__NAV_QUIZ__",
        "register": "__NAV_REGISTER__",
        "sources": "__NAV_SOURCES__",
        "methodology": "__NAV_METHOD__",
        "ballot": "__NAV_BALLOT__",
    }
    for key, token in nav.items():
        template = template.replace(token, ' aria-current="page"' if key == active else "")
    page = (
        template.replace("__TITLE__", e(title))
        .replace("__DESCRIPTION__", e(description))
        .replace("__CONTENT__", content)
        .replace("__GENERATED__", e(generated))
    )
    if "__" in page:
        raise BuildError("unreplaced placeholder in static template")
    return page


def write_pages(pages: dict[str, str], target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for name, content in pages.items():
        (target / name).write_text(content, encoding="utf-8")


def copy_data(target: Path) -> None:
    data_dir = target / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    for name in DATA_FILES:
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
        if "__" in text:
            raise BuildError(f"placeholder left in {name}")
        if "<!--" in text and "__" in text:
            raise BuildError(f"template artifact in {name}")
    quiz = (DIST / "quiz_v21.html").read_text("utf-8")
    for marker in ("V21Engine", "selected_topics", "app-data", "register-data", "meta-data"):
        if marker not in quiz:
            raise BuildError(f"quiz missing {marker}")
    for name in ("index.html", "register_2027.html", "sources_2027.html", "methodology_2027.html", "ballot_2027.html"):
        text = (DIST / name).read_text("utf-8")
        if "Oficialus 2027 m. biuletenis dar nepaskelbtas" not in text and name != "ballot_2027.html":
            raise BuildError(f"{name}: missing status disclosure")
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
