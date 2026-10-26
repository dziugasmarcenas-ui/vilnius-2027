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
REVIEW_FILES = ["source_manifest_v21.json", "source_checks_v21.json", "human_validation_v21.json", "vrk_2027_manifest.json"]


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


def load_all() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    data = load_data()
    check_evidence(data)
    register = json.loads(REGISTER.read_text("utf-8"))
    if register.get("format") != "vaa-vrk-2027-register-v1":
        raise BuildError("register format invalid")
    lists = register.get("vilnius", {}).get("lists", [])
    if len(lists) != 4:
        raise BuildError("register must contain exactly four Vilnius lists")
    if register.get("official_status", {}).get("candidate_lists_published") is not False:
        raise BuildError("build expects final candidate lists not to be published")
    if register.get("official_status", {}).get("ballot_numbers_published") is not False:
        raise BuildError("build expects ballot numbers not to be published")
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
    return data, register, sources, manifest, checks, mb


def trimmed_register(register: dict[str, Any]) -> dict[str, Any]:
    vilnius = register["vilnius"]
    lists = []
    for row in vilnius["lists"]:
        mayor = row.get("mayor_candidate")
        lists.append(
            {
                "party_id": row["party_id"],
                "party_name": row["party_name"],
                "list_name": row["list_name"],
                "registered_on": row["registered_on"],
                "decision": row["decision"],
                "detail_url": row["detail_url"],
                "mayor_candidate": (
                    {
                        "name": mayor.get("name"),
                        "registered_on": mayor.get("registered_on"),
                        "decision": mayor.get("decision"),
                        "detail_url": mayor.get("detail_url"),
                    }
                    if mayor
                    else None
                ),
                "candidate_names_published": row.get("candidate_names_published", False),
                "ballot_number": row.get("ballot_number"),
            }
        )
    return {
        "election": register["election"],
        "official_status": register["official_status"],
        "vilnius": {
            "apygarda": vilnius["apygarda"],
            "registered_lists_count": vilnius["registered_lists_count"],
            "registered_mayor_candidates_count": vilnius["registered_mayor_candidates_count"],
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
            "unknown_rows": len(data["statements"]) * len(data["parties"]) - known,
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
      Šiandien lyginame {len(register['vilnius']['lists'])} oficialiai užregistruotus Vilniaus miesto sąrašus ir tik turimus 2027 m. programinius dokumentus.
      {internal('register_2027.html', 'Žiūrėti registrą →')}
    </div>
    """


def index_content(
    data: dict[str, Any],
    register: dict[str, Any],
    sources: dict[str, Any],
    checks: dict[str, Any],
) -> str:
    parties = party_map(data)
    lists = register["vilnius"]["lists"]
    st = register["official_status"]
    list_rows = []
    for row in lists:
        party = parties.get(row["party_id"], {})
        mayor = row.get("mayor_candidate")
        mayor_text = (
            f"<b>{e(mayor['name'].title())}</b><span>mero kandidatas</span>"
            if mayor
            else "<b>—</b><span>mero kandidatas dar neužregistruotas</span>"
        )
        list_rows.append(
            f"""
            <div class="party-row">
              <span class="party-dot" style="background:{e(party.get('color', '#777'))}"></span>
              <div><div class="name">{e(party.get('short_name', row['party_id']))}</div>
              <div class="full">{e(row['party_name'])}</div></div>
              <div class="mayor">{mayor_text}</div>
            </div>
            """
        )
    feature_cards = [
        (
            "01",
            "Keturi registruoti sąrašai",
            "Lyginame tik tuos Vilniaus miesto sąrašus, kuriuos VRK buvo užregistravęs 2026-10-26. Jokių spėjamų kandidatų.",
        ),
        (
            "02",
            "Citata, data, nuoroda",
            "Kiekviena įskaitoma pozicija turi verbatim citatą ir šaltinį. Nežinoma pozicija nėra priešingybė ir negauna neigiamo balo.",
        ),
        (
            "03",
            "Sprendimas pagal temą",
            "Rodome ne vieną paslaptingą reitingą, o atitikimą tema po temos, aprėptį ir trūkstamus duomenis.",
        ),
        (
            "04",
            "Balsavimo planas",
            "Paaiškiname, kaip atrodo tarybos ir mero biuleteniai, bet aiškiai pasakome, kad oficialūs pavyzdžiai dar nepaskelbti.",
        ),
    ]
    feature_html = "".join(
        f'<div class="card flat"><div class="kicker">{e(n)}</div><h3>{e(t)}</h3><p>{e(d)}</p></div>'
        for n, t, d in feature_cards
    )
    return f"""
    <section class="hero">
      <span class="eyebrow"><span class="dot"></span>Vilnius 2027 · taryba ir meras</span>
      <h1>Ką apie Vilnių žada partijos – ir ką tai reiškia tau?</h1>
      <p class="lead">Atsakyk į 13 teiginių apie transportą, švietimą, būstą, skaidrumą, saugumą ir verslą. Parodysime, kurie oficialiai registruoti sąrašai turi cituojamas 2027 m. pozicijas ir kiek jos sutampa su tavo prioritetais.</p>
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
      <p>VRK 2027 m. savivaldybių rinkimų ciklas dar nepasiekė galutinių kandidatų sąrašų. Šiame etape galime sąžiningai lyginti tik registruotus sąrašus ir jų paskelbtus programinius dokumentus.</p>
      <ol class="timeline">
        <li class="done"><b>2026-10-07</b><span>Prasidėjo politinė kampanija. LP ir LS jau turi registruotus sąrašus.</span></li>
        <li class="next"><b>2026-10-28</b><span>Baigiamas savarankiškų kampanijos dalyvių registravimas. Sąrašų skaičius dar gali keistis.</span></li>
        <li class="future"><b>2026-12-17 17:00</b><span>Baigiamas kandidatų pareiškinių dokumentų pateikimas.</span></li>
        <li class="future"><b>2027-02-04</b><span>VRK priima sprendimus registruoti kandidatų sąrašus arba jų neregistruoti.</span></li>
        <li class="future"><b>2027-02-05</b><span>VRK paskelbia kandidatų sąrašus, kandidatus ir sąrašų rinkimų numerius.</span></li>
        <li class="future"><b>2027-03-07</b><span>Savivaldybių tarybų ir merų rinkimai.</span></li>
      </ol>
      <p class="small mt1">Datos iš oficialaus VRK 2027 m. rinkimų grafiko. {ext(st['candidate_lists_published_by']['source_url'], 'VRK grafikas')}</p>
    </section>

    <section class="card mt2">
      <div class="kicker">Registruoti Vilniaus sąrašai</div>
      <h2>Keturi sąrašai, du mero kandidatai</h2>
      <p>Tai VRK 2026-10-26 registracijų būsena, o ne galutinis biuletenis. Kandidatų pavardžių ir sąrašų numerių dar nėra.</p>
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
          <li><strong>Galima:</strong> pasakyti, kad vienas sąrašas pagal turimus duomenis labiau atitinka tavo prioritetus.</li>
          <li><strong>Negalima:</strong> teigti, kad tai galutinis biuletenis, rinkimų prognozė ar oficiali rekomendacija.</li>
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
          <li><span>Registruoti Vilniaus sąrašai</span><b>{len(lists)}</b></li>
          <li><span>Registruoti mero kandidatai</span><b>{register['vilnius']['registered_mayor_candidates_count']}</b></li>
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
    rows = []
    for row in register["vilnius"]["lists"]:
        party = parties.get(row["party_id"], {})
        mayor = row.get("mayor_candidate")
        mayor_name = mayor["name"].title() if mayor else "—"
        rows.append(
            f"""
            <tr>
              <td><strong>{e(party.get('short_name', row['party_id']))}</strong><br><span class="small">{e(row['party_name'])}</span></td>
              <td>{e(row['registered_on'])}<br><span class="small">{e(row['decision'])}</span></td>
              <td>{e(mayor_name)}</td>
              <td>—<br><span class="small">nepaskelbtas</span></td>
              <td>—<br><span class="small">nepaskelbti</span></td>
              <td>{ext(row['detail_url'], 'VRK įrašas')}</td>
            </tr>
            """
        )
    statuses = [
        ("Galutiniai kandidatų sąrašai", "dar nepaskelbti", "iki 2027-02-05"),
        ("Kandidatų pavardės", "dar nepaskelbtos", "numatoma 2027-02-05"),
        ("Sąrašų rinkimų numeriai", "dar nepaskelbti", "numatoma 2027-02-05"),
        (
            "Kandidatų programų leidiniai",
            "Informacija ruošiama" if st.get("programme_publications_status") == "pending" else "nepaskelbti",
            ext(st["programme_publications_url"], "VRK puslapis"),
        ),
        (
            "Biuletenių pavyzdžiai",
            "Informacija ruošiama" if st.get("ballot_examples_status") == "pending" else "nepaskelbti",
            ext(st["ballot_examples_url"], "VRK puslapis"),
        ),
    ]
    status_html = "".join(
        f"<li><span>{e(label)}</span><div><b>{e(value)}</b><span class='small'>{value2}</span></div></li>"
        for label, value, value2 in statuses
    )
    return f"""
    <section class="hero">
      <span class="eyebrow"><span class="dot"></span>Oficialus registras · 2026-10-26</span>
      <h1>Kas šiandien tikrai užregistruota?</h1>
      <p class="lead">Šis puslapis atskiria tris dalykus: kampanijos dalyvius, registruotus sąrašus ir galutinį biuletenį. 2026-10-26 VRK buvo užregistravusi keturis Vilniaus miesto sąrašus, bet ne galutinius kandidatų sąrašus ar numerius.</p>
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
        <p><strong>Dalyvis</strong> – partija ar komitetas, kurį VRK registravo kampanijai. <strong>Sąrašas</strong> – oficialiai registruotas kandidatų sąrašas. <strong>Biuletenis</strong> – galutinis dokumentas su numeriais ir pavardėmis, kurį rinkėjas mato balsavimo vietoje.</p>
        <p class="small">Šiame etape galime sąžiningai lyginti tik registruotų sąrašų paskelbtas 2027 m. pozicijas. Negalime išgalvoti kandidatų, numerių ar vietų sąraše.</p>
        <div class="inline-links">
          {internal('sources_2027.html', 'Registro šaltiniai', 'btn btn-soft btn-sm')}
          {internal('quiz_v21.html', 'Grįžti į klausimyną', 'btn btn-ghost btn-sm')}
        </div>
      </div>
    </section>

    <section class="card mt2">
      <div class="kicker">Vilniaus miesto apygarda Nr. 57</div>
      <h2>Registruoti sąrašai</h2>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Sąrašas</th><th>Registracija</th><th>Mero kandidatas</th><th>Sąrašo numeris</th><th>Kandidatų pavardės</th><th>Šaltinis</th></tr></thead>
          <tbody>{''.join(rows)}</tbody>
        </table>
      </div>
      <p class="small mt2">Lentelėje – tik VRK 2026-10-26 būsena. Kandidatų sąrašai ir numeriai bus paskelbti atskirai; tada ši lentelė turi būti atnaujinta.</p>
    </section>

    <section class="card mt2">
      <div class="kicker">Oficialūs šaltiniai</div>
      <h2>Ką tikrinome</h2>
      <ul class="list-clean">
        <li>{ext(register['election']['official_page'], '2027 m. rinkimų bendra informacija')} – rinkimų data, apygarda, tarybos narių skaičius.</li>
        <li>{ext(st['candidate_lists_published_by']['source_url'], 'VRK 2027 m. rinkimų grafikas')} – registracijos, nominacijos ir paskelbimo datos.</li>
        <li>{ext(register['source']['index_url'], 'Kampanijos dalyvių sąrašas')} – 52 nacionaliniai dalyviai, 4 Vilniaus miesto sąrašai.</li>
        <li>{ext(st['programme_publications_url'], 'Kandidatų rinkimų programų leidiniai')} – „Informacija ruošiama“.</li>
        <li>{ext(st['ballot_examples_url'], 'Biuletenių pavyzdžiai ir kiti leidiniai')} – „Informacija ruošiama“.</li>
      </ul>
      <p class="small mt2">Registro šaltinio SHA-256 hash’ai ir HTTP metadata publikuojami {internal('data/vrk_2027_manifest.json', 'VRK registro manifeste')}. Žalias scraped HTML lieka privačiame peržiūros pakete ir nėra publikuojamas. Citatų patikra – {len(checks['checks'])} eilutės.</p>
    </section>

    <section class="card mt2">
      <div class="kicker">Ribos</div>
      <h2>Ko šis registras negali pasakyti</h2>
      <ul class="list-clean">
        <li>Negali pasakyti, kas bus galutiniame biuletenyje – sąrašai dar gali keistis iki 2027-02-04.</li>
        <li>Negali pasakyti sąrašo numerio – VRK jį paskelbs kartu su galutiniais sąrašais.</li>
        <li>Negali pasakyti, ar partija turi poziciją konkrečiu klausimu – tam reikia atskiros citatos.</li>
        <li>Negali pakeisti oficialaus VRK biuletenio balsavimo vietoje.</li>
      </ul>
    </section>
    """


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
                quote_cell = f'<span class="small">{e(answer.get("note", "Nėra cituojamos pozicijos."))}</span>'
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
        1 for rows in data["answers"].values() for answer in rows.values() if answer.get("scoreable")
    )
    mb_quote_count = len(mb["methodology"]["quotes"])
    current_sources = [s for s in sources["sources"] if not s["id"].startswith("manobalsas_")]
    context_sources = [s for s in sources["sources"] if s["id"].startswith("manobalsas_")]
    return f"""
    <section class="hero">
      <span class="eyebrow"><span class="dot"></span>Šaltiniai ir citatų patikra</span>
      <h1>Kiekvienas balas turi pėdsaką.</h1>
      <p class="lead">Šis puslapis rodo, iš ko sudarytas klausimynas: oficialų VRK registrą, 2027 m. partijų dokumentus, kiekvienos citatos šaltinį ir tai, kur pozicijos nėra. Tai techninė citatų patikra, ne nepriklausoma teisinė ar politinė peržiūra.</p>
      <div class="banner"><strong>Oficialus 2027 m. biuletenis dar nepaskelbtas.</strong> VRK galutinius kandidatų sąrašus ir numerius numato paskelbti iki 2027-02-05. Šiame puslapyje – tik turimi 2027 m. programiniai dokumentai ir registruoti sąrašai.</div>
      <div class="banner green"><strong>{len(sources['sources'])} šaltiniai · {len(checks['checks'])} patikrintos citatos.</strong> {len(current_sources)} – 2027 m. dokumentai, {len(context_sources)} – Mano Balsas 2024 kontekstas. Visi citatų įrašai turi būti randami išsaugotame šaltinio tekste. Nepriklausoma žmogaus peržiūra dar neatlikta.</div>
    </section>

    <section class="grid grid-4">
      <div class="card flat"><div class="kicker">Šaltiniai</div><h2>{len(sources['sources'])}</h2><p class="small">{len(current_sources)} 2027 m. dokumentai ir {len(context_sources)} Mano Balsas konteksto šaltiniai.</p></div>
      <div class="card flat"><div class="kicker">Citatos</div><h2>{len(checks['checks'])}</h2><p class="small">{dataset_quotes} dataset eilutės + {mb_quote_count} Mano Balsas metodikos citatos.</p></div>
      <div class="card flat"><div class="kicker">Įskaitoma</div><h2>{scoreable_rows}</h2><p class="small">Tik šios eilutės patenka į balą.</p></div>
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
      <div class="kicker">Kontekstas, ne 2027 m. pozicija</div>
      <h2>Mano Balsas 2024</h2>
      <p>Mano Balsas 2026-10-26 neturėjo 2027 m. Vilniaus savivaldybės testo. Jo 2024 m. nacionalinė metodika ir klausimynas naudojami kaip klausimyno dizaino įkvėpimas, ne kaip partijų vietos pažadai.</p>
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
      </div>
    </section>
    """


def methodology_content(
    data: dict[str, Any],
    register: dict[str, Any],
    mb: dict[str, Any],
    human: dict[str, Any],
) -> str:
    scoreable_stances = [
        answer["stance"]
        for rows in data["answers"].values()
        for answer in rows.values()
        if answer.get("scoreable") is True and answer.get("quote_verified") is True
    ]
    all_agree = bool(scoreable_stances) and all(stance == "agree" for stance in scoreable_stances)
    stance_limit = (
        f'<li><strong>Viena pozicijų kryptis.</strong> Visos {len(scoreable_stances)} įskaitomos 2026-10-26 pozicijos yra „sutinku“; nėra įskaitomos priešingos pozicijos. Todėl rezultatas labiau rodo temų aprėptį negu priešingas stovyklas.</li>'
        if all_agree
        else ""
    )
    return f"""
    <section class="hero">
      <span class="eyebrow"><span class="dot"></span>Metodika · v21</span>
      <h1>Kaip iš atsakymų gaunamas sąrašas – ir kodėl jis nėra galutinis.</h1>
      <p class="lead">Metodika sukurta taip, kad nežinoma pozicija neliktų paslėpta, o skirtingas partijų duomenų kiekis nebūtų maskuojamas vienu skaičiumi. Rezultatas yra dalinė, cituojama suvestinė – ne rinkimų prognozė ir ne teisinė konsultacija.</p>
      <div class="banner"><strong>Oficialus 2027 m. biuletenis dar nepaskelbtas.</strong> Numatoma, kad galutiniai sąrašai ir numeriai bus paskelbti iki 2027-02-05. Iki tol klausimynas lygina {len(register['vilnius']['lists'])} registruotus Vilniaus miesto sąrašus, o ne galutinį biuletenį.</div>
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
        <p>Jei geriausias atitikimas nesiekia 50/100, sąrašo nerekomenduojame net tada, kai jis techniškai surenka daugiausia taškų. Tai reiškia, kad turimos pozicijos labiau prieštarauja tavo atsakymams arba jų per mažai prasmingam palyginimui.</p>
        <p>Tai sąmoningas sprendimas: skirtingas duomenų kiekis gali sukurti dirbtinį pranašumą. Geriau parodyti artumą ir spragas nei apsimesti, kad skirtumas tikslus.</p>
      </div>
    </section>

    <section class="card mt2">
      <div class="kicker">Balso planas</div>
      <h2>Ką reiškia rezultatas biuletenyje</h2>
      <ol class="steps">
        <li><b>Tarybos sąrašas.</b> Jei rezultatas „ryškus“, rodome labiausiai tinkantį sąrašą. Jei „arti“ – 2–3 sąrašus ir temų palyginimą.</li>
        <li><b>Pirmumo balsai.</b> Jie skiriami kandidatams tame pačiame sąraše. 2023 m. buvo iki 5; 2027 m. oficialų skaičių ir žymėjimą reikia patikrinti biuletenyje.</li>
        <li><b>Mero biuletenis.</b> Meras renkamas atskirai. Jei sąrašas neturi registruoto mero kandidato, mero biuletenyje reikia rinktis atskirai.</li>
        <li><b>Oficialus biuletenis.</b> Galutinis sprendimas visada priklauso nuo VRK biuletenio balsavimo vietoje.</li>
      </ol>
      <div class="banner mt2"><strong>Teisinė išlyga:</strong> balsavimo žymėjimo taisyklės dar nėra oficialiai paskelbtos 2027 m. biuleteniui. Šis puslapis nekeičia Rinkimų kodekso ir nėra teisinė konsultacija.</div>
    </section>

    <section class="card mt2">
      <div class="kicker">Mano Balsas</div>
      <h2>Kas paimta iš 2024 m. metodikos</h2>
      <p>Mano Balsas naudoja partijų klausimyną ir ekspertinį kodavimą. 2026-10-26 jo svetainėje nebuvo 2027 m. Vilniaus savivaldybės testo. Todėl 2024 m. nacionalinė medžiaga čia naudojama tik kaip dizaino įkvėpimas:</p>
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


def ballot_content(data: dict[str, Any], register: dict[str, Any]) -> str:
    st = register["official_status"]
    return f"""
    <section class="hero">
      <span class="eyebrow"><span class="dot"></span>Balsavimas · 2027-03-07</span>
      <h1>Kaip pažymėti biuletenį, kai jis dar nepaskelbtas?</h1>
      <p class="lead">2027 m. kovo 7 d. balsuosi dėl Vilniaus miesto tarybos ir mero. Tai du atskiri biuleteniai. Šis puslapis paaiškina struktūrą, bet oficialūs pavyzdžiai VRK puslapyje dar pažymėti „Informacija ruošiama“.</p>
      <div class="banner"><strong>Oficialūs biuletenių pavyzdžiai dar nepaskelbti.</strong> VRK puslapis {ext(st['ballot_examples_url'], '„Biuletenių pavyzdžiai ir kiti leidiniai“')} 2026-10-26 rodė „Informacija ruošiama“. Nenaudok šio puslapio kaip biuletenio kopijos.</div>
    </section>

    <section class="grid grid-2">
      <div class="card">
        <div class="kicker">Tarybos biuletenis</div>
        <h2>Vienas sąrašas + pirmumo balsai</h2>
        <p>Tarybos biuletenyje pasirenki vieną partijos ar komiteto sąrašą. Toje pačioje dalyje gali skirti pirmumo balsus to sąrašo kandidatams.</p>
        <div class="ballot-demo">
          <div class="ballot-paper" aria-label="Iliustracinis tarybos biuletenio pavyzdys, ne oficialus">
            <div class="ballot-title">Iliustracija · ne oficialus biuletenis</div>
            <div class="ballot-row"><span class="ballot-box">1</span><span>Sąrašas A</span><span class="ballot-prefs"><span class="pref">1</span><span class="pref">2</span><span class="pref">3</span><span class="pref">4</span><span class="pref">5</span></span></div>
            <div class="ballot-row"><span class="ballot-box">2</span><span>Sąrašas B</span><span class="ballot-prefs"><span class="pref">1</span><span class="pref">2</span><span class="pref">3</span><span class="pref">4</span><span class="pref">5</span></span></div>
            <div class="ballot-row"><span class="ballot-box">3</span><span>Sąrašas C</span><span class="ballot-prefs"><span class="pref">1</span><span class="pref">2</span><span class="pref">3</span><span class="pref">4</span><span class="pref">5</span></span></div>
            <div class="ballot-label">Skaičiai 1–5 iliustruoja pirmumo balsus. 2027 m. oficialų skaičių ir žymėjimą patikrink biuletenyje.</div>
          </div>
        </div>
        <p class="small mt2">2023 m. savivaldybių rinkimuose buvo galima skirti iki 5 pirmumo balsų. 2027 m. tvarka turi būti patvirtinta oficialiame biuletenyje; šis puslapis to negarantuoja.</p>
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
        <p class="small mt2">2026-10-26 VRK registre buvo du Vilniaus mero kandidatai: Vytautas Mitalas (LP) ir Simonas Gentvilas (LS). Kiti gali būti registruojami vėliau.</p>
      </div>
    </section>

    <section class="card mt2">
      <div class="kicker">Žingsnis po žingsnio</div>
      <h2>Ką daryti balsavimo dieną</h2>
      <ol class="steps">
        <li><b>Pasitikrink apylinkę.</b> Vilniaus miesto rinkimų apygarda Nr. 57. Balsavimo vieta priklauso nuo tavo deklaruotos gyvenamosios vietos.</li>
        <li><b>Gauk du biuletenius.</b> Vieną tarybai, vieną merui. Jie gali būti skirtingose biuletenio dalyse.</li>
        <li><b>Taryboje pažymėk vieną sąrašą.</b> Sąrašo numeris bus oficialiame biuletenyje, ne šiame puslapyje.</li>
        <li><b>Pirmumo balsus skirk sąmoningai.</b> Jie keičia kandidatų eilę sąraše. 2023 m. buvo iki 5; 2027 m. oficialų skaičių patikrink biuletenyje.</li>
        <li><b>Mero biuletenyje pažymėk vieną kandidatą.</b> Tai atskiras sprendimas nuo tarybos sąrašo.</li>
        <li><b>Įmesk biuletenius į tam skirtas urnas.</b> Jei sugadinai – kreipkis į rinkimų komisiją dėl naujo biuletenio; tvarka priklauso nuo apylinkės.</li>
      </ol>
      <div class="callout warn mt2"><strong>Nedaryk:</strong> nežymėk kelių sąrašų tarybos biuletenyje, nerašyk savo pavardės, nefotografuok pažymėto biuletenio, neimk biuletenio iš apylinkės. Oficialias taisykles visada patikrink VRK informacijoje.</div>
    </section>

    <section class="grid grid-2 mt2">
      <div class="card">
        <div class="kicker">Kur balsuoti</div>
        <h2>Oficiali informacija</h2>
        <ul class="list-clean">
          <li>{ext(register['election']['official_page'], '2027 m. savivaldybių tarybų ir merų rinkimai')} – VRK bendra informacija.</li>
          <li>{ext('https://www.vrk.lt/lt/2027-m-kovo-7-d-savivaldybiu-tarybu-nariu-ir-meru-rinkimai/balsavimas-savivaldybese', 'Balsavimas savivaldybėse')} – VRK balsavimo tvarka.</li>
          <li>{ext('https://www.vrk.lt/lt/2027-m-kovo-7-d-savivaldybiu-tarybu-nariu-ir-meru-rinkimai/biuleteniu-pavyzdziai-ir-kiti-leidiniai', 'Biuletenių pavyzdžiai')} – kol kas „Informacija ruošiama“.</li>
          <li>{ext('https://www.vrk.lt/lt/2027-m-kovo-7-d-savivaldybiu-tarybu-nariu-ir-meru-rinkimai/mokomoji-medziaga', 'Mokomoji medžiaga')} – VRK paaiškinimai.</li>
        </ul>
      </div>
      <div class="card">
        <div class="kicker">Teisinė išlyga</div>
        <h2>Ko šis puslapis negali</h2>
        <ul class="list-clean">
          <li>Pakeisti oficialaus VRK biuletenio.</li>
          <li>Patvirtinti 2027 m. pirmumo balsų skaičiaus, kol jis nepaskelbtas.</li>
          <li>Pasakyti, kas bus galutiniame sąraše ar koks bus sąrašo numeris.</li>
          <li>Būti teisinė konsultacija konkrečiu ginču ar pažeidimu.</li>
        </ul>
        <p class="small mt2">Balsavimo tvarka gali keistis. Prieš rinkimus patikrink VRK puslapį ir savo apylinkės pranešimą.</p>
      </div>
    </section>

    <section class="card mt2">
      <div class="kicker">Kaip tai siejasi su klausimynu</div>
      <h2>Rezultatas padeda pasirinkti, bet nepažymi biuletenio</h2>
      <p>Klausimynas parodo, kurie sąrašai labiausiai atitinka tavo prioritetus pagal turimas cituojamas pozicijas. Jis neperkelia atsakymų į biuletenį ir negarantuoja, kad partija įgyvendins pažadus.</p>
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
    quiz = template.replace("/*__DATA__*/", json_script(data))
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


def build() -> dict[str, str]:
    data, register, sources, manifest, checks, mb = load_all()
    human = load_human()
    pages = {
        "index.html": static_page(
            "Rinkimų įrankis",
            "Vilniaus 2027 m. tarybos ir mero rinkimų įrankis: registruoti sąrašai, cituojamos pozicijos ir balsavimo planas.",
            index_content(data, register, sources, checks),
            "index",
            data["generated_at"],
        ),
        "quiz_v21.html": build_quiz(data, register, manifest, checks, mb),
        "register_2027.html": static_page(
            "VRK registras",
            "Oficialus VRK 2027 m. Vilniaus miesto sąrašų registras 2026-10-26 būsena.",
            register_content(data, register, checks),
            "register",
            data["generated_at"],
        ),
        "sources_2027.html": static_page(
            "Šaltiniai",
            "2027 m. Vilniaus partijų pozicijų šaltiniai, citatų patikra ir Mano Balsas kontekstas.",
            sources_content(data, sources, manifest, checks, mb, human),
            "sources",
            data["generated_at"],
        ),
        "methodology_2027.html": static_page(
            "Metodika",
            "Kaip v21 klausimynas skaičiuoja atitikimą, aprėptį ir balsavimo planą.",
            methodology_content(data, register, mb, human),
            "methodology",
            data["generated_at"],
        ),
        "ballot_2027.html": static_page(
            "Balsavimas",
            "2027 m. kovo 7 d. Vilniaus tarybos ir mero biuletenių struktūra, pirmumo balsai ir oficialūs VRK šaltiniai.",
            ballot_content(data, register),
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
        "data/vrk_2027_manifest.json",
        "data/sources_2027_v21.json",
        ".nojekyll",
    ]
    for base in (ROOT, DIST):
        for name in required:
            path = base / name
            if not path.exists():
                raise BuildError(f"missing build output: {path.relative_to(ROOT)}")
            if path.stat().st_size < 200 and name != ".nojekyll":
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
