#!/usr/bin/env python3
"""Fetch, hash and quote-check the 2027 evidence sources.

A passing check means: the URL returned HTTPS 200, the extracted text was saved
under its SHA-256, and the exact quotation occurs in that text after layout
normalisation.  It does not mean the quotation is a good interpretation of the
programme.  That second step is deliberately left to human review.

Usage:
  python3 vaa_sources_v21.py --refresh [--force]
  python3 vaa_sources_v21.py --check
  python3 vaa_sources_v21.py --report
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vaa_fetch_v21 import (
    CACHE,
    ROOT,
    check_row_hashes,
    fetch,
    normalise,
    quote_present,
    read_cached_text,
    sha256_text,
)

REGISTRY = ROOT / "vaa" / "sources_2027_v21.json"
DATASET = ROOT / "vaa" / "vilnius_2027_v21.json"
MB_CONTEXT = ROOT / "vaa" / "manobalsas_2024_context.json"
MANIFEST = ROOT / "vaa" / "review" / "source_manifest_v21.json"
CHECKS = ROOT / "vaa" / "review" / "source_checks_v21.json"
MANIFEST_FORMAT = "vaa-source-manifest-v21"
CHECKS_FORMAT = "vaa-source-checks-v21"


class SourceError(ValueError):
    pass


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text("utf-8"))


def _registry_rows() -> list[dict[str, Any]]:
    doc = _load(REGISTRY)
    if doc.get("format") != "vaa-source-registry-v21":
        raise SourceError("source registry format invalid")
    rows = doc.get("sources")
    if not isinstance(rows, list) or not rows:
        raise SourceError("source registry has no sources")
    seen = set()
    for row in rows:
        sid = row.get("id")
        if not sid or sid in seen:
            raise SourceError(f"invalid or duplicate source id: {sid}")
        seen.add(sid)
        if not str(row.get("url", "")).startswith("https://"):
            raise SourceError(f"{sid}: https URL required")
        for key in ("publisher", "published", "label", "kind"):
            if not row.get(key):
                raise SourceError(f"{sid}: {key} required")
    return rows


def _evidence_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for party_id, answers in data.get("answers", {}).items():
        for statement_id, answer in answers.items():
            if not isinstance(answer, dict):
                raise SourceError(f"{party_id}/{statement_id}: answer must be an object")
            quote = answer.get("quote")
            if not quote:
                continue
            rows.append(
                {
                    "evidence_id": f"{party_id}__{statement_id}",
                    "party_id": party_id,
                    "statement_id": statement_id,
                    "quote": quote,
                    "source_id": answer.get("source_id"),
                    "source_url": answer.get("source_url"),
                    "scoreable": bool(answer.get("scoreable")),
                }
            )
    return rows


def _context_rows() -> list[dict[str, Any]]:
    if not MB_CONTEXT.exists():
        return []
    doc = _load(MB_CONTEXT)
    rows = []
    for index, item in enumerate(doc.get("quotes", [])):
        if not item.get("quote"):
            continue
        rows.append(
            {
                "evidence_id": f"manobalsas_context__{index}",
                "party_id": "context_only",
                "statement_id": item.get("id", f"context_{index}"),
                "quote": item["quote"],
                "source_id": item.get("source_id", "manobalsas_methodology"),
                "source_url": item.get("source_url"),
                "scoreable": False,
            }
        )
    return rows


def refresh(force: bool = False) -> None:
    if MANIFEST.exists() and not force:
        raise SourceError(
            f"{MANIFEST.relative_to(ROOT)} already exists; pass --force only after reviewing the change"
        )
    registry = _registry_rows()
    data = _load(DATASET)
    evidence = _evidence_rows(data)
    context = _context_rows()
    needed = {row["source_id"] for row in evidence + context if row.get("source_id")}
    unknown = needed - {row["id"] for row in registry}
    if unknown:
        raise SourceError("dataset references unregistered sources: " + ", ".join(sorted(unknown)))

    rows = []
    texts: dict[str, str] = {}
    for source in registry:
        got = fetch(source["url"])
        row = {key: value for key, value in got.items() if key not in ("text", "raw")}
        row.update(
            source_id=source["id"],
            publisher=source["publisher"],
            published=source["published"],
            label=source["label"],
            kind=source["kind"],
        )
        rows.append(row)
        texts[source["id"]] = got.get("text", "")
        status = row.get("http_status")
        print(f"{source['id']:26} HTTP {status}  {row.get('text_sha256') or '—'}")
        if status != 200:
            print("  warning:", "; ".join(row.get("extraction_warnings", [])))

    checks = []
    checked_at = datetime.now(timezone.utc).isoformat()
    failures = []
    for item in evidence + context:
        sid = item.get("source_id")
        text = texts.get(sid, "")
        found = quote_present(text, item["quote"]) if text else False
        row = next((r for r in rows if r["source_id"] == sid), None)
        checks.append(
            {
                "evidence_id": item["evidence_id"],
                "party_id": item["party_id"],
                "statement_id": item["statement_id"],
                "quote": item["quote"],
                "quote_sha256": sha256_text(normalise(item["quote"])),
                "quote_found": found,
                "scoreable": item["scoreable"],
                "source_id": sid,
                "source_url": item["source_url"],
                "source_content_sha256": row.get("content_sha256") if row else None,
                "source_text_sha256": row.get("text_sha256") if row else None,
                "source_http_status": row.get("http_status") if row else None,
                "checked_at": checked_at,
            }
        )
        if not found:
            failures.append(item)
            print("  MISSING QUOTE:", item["evidence_id"], "->", item["quote"][:100])
    if failures:
        # Keep the manifest but do not write a passing checks file.
        MANIFEST.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST.write_text(
            json.dumps(
                {
                    "format": MANIFEST_FORMAT,
                    "generated_at": checked_at,
                    "sources": rows,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        raise SourceError(f"{len(failures)} quotation(s) not found; checks not written")

    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(
        json.dumps(
            {
                "format": MANIFEST_FORMAT,
                "generated_at": checked_at,
                "sources": rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    CHECKS.write_text(
        json.dumps(
            {
                "format": CHECKS_FORMAT,
                "generated_at": checked_at,
                "checks": checks,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"OK: {len(rows)} sources, {len(checks)} quotations checked, "
        f"{sum(1 for c in checks if c['quote_found'])} found"
    )


def validate_manifest(doc: dict[str, Any]) -> None:
    if doc.get("format") != MANIFEST_FORMAT:
        raise SourceError("manifest format invalid")
    rows = doc.get("sources")
    if not isinstance(rows, list) or not rows:
        raise SourceError("manifest has no sources")
    seen = set()
    for row in rows:
        sid = row.get("source_id")
        if not sid or sid in seen:
            raise SourceError(f"invalid or duplicate manifest source: {sid}")
        seen.add(sid)
        if not re.fullmatch(r"[0-9a-f]{64}", row.get("content_sha256") or ""):
            raise SourceError(f"{sid}: content hash missing")
        if row.get("http_status") != 200:
            raise SourceError(f"{sid}: source was not retrieved successfully")


def check() -> None:
    manifest = _load(MANIFEST)
    validate_manifest(manifest)
    checks_doc = _load(CHECKS)
    if checks_doc.get("format") != CHECKS_FORMAT:
        raise SourceError("checks format invalid")
    data = _load(DATASET)
    evidence = _evidence_rows(data)
    if not evidence:
        raise SourceError("dataset has no quotations")
    check_by_id = {row["evidence_id"]: row for row in checks_doc.get("checks", [])}
    missing = [row["evidence_id"] for row in evidence if row["evidence_id"] not in check_by_id]
    if missing:
        raise SourceError("checks missing evidence: " + ", ".join(missing))
    manifest_by_id = {row["source_id"]: row for row in manifest["sources"]}
    for row in evidence:
        check_row = check_by_id[row["evidence_id"]]
        if not check_row.get("quote_found"):
            raise SourceError(f"{row['evidence_id']}: quotation not found")
        if check_row.get("source_id") != row.get("source_id"):
            raise SourceError(f"{row['evidence_id']}: source id mismatch")
        if check_row.get("source_url") != row.get("source_url"):
            raise SourceError(f"{row['evidence_id']}: source url mismatch")
        manifest_row = manifest_by_id.get(row.get("source_id"))
        if not manifest_row:
            raise SourceError(f"{row['evidence_id']}: source missing from manifest")
        if check_row.get("source_content_sha256") != manifest_row.get("content_sha256"):
            raise SourceError(f"{row['evidence_id']}: source content hash changed")
        if check_row.get("source_text_sha256") != manifest_row.get("text_sha256"):
            raise SourceError(f"{row['evidence_id']}: source text hash changed")
        if row.get("scoreable") and not all(
            [check_row.get("quote_found"), check_row.get("source_http_status") == 200]
        ):
            raise SourceError(f"{row['evidence_id']}: scoreable evidence failed source gate")
        text = read_cached_text(manifest_row, CACHE)
        if not quote_present(text, row["quote"]):
            raise SourceError(f"{row['evidence_id']}: quote no longer present in cached text")
    for row in manifest["sources"]:
        errors = check_row_hashes(row, CACHE)
        if errors:
            raise SourceError("; ".join(errors))
    print(
        f"OK: {len(manifest['sources'])} sources, {len(evidence)} dataset quotations, "
        f"all cached hashes and quotes verified"
    )


def report() -> None:
    data = _load(DATASET)
    checks_doc = _load(CHECKS)
    check_by_id = {row["evidence_id"]: row for row in checks_doc.get("checks", [])}
    counts = {"scoreable": 0, "partial": 0, "unknown": 0}
    for party_id, answers in data["answers"].items():
        known = 0
        scoreable = 0
        for statement_id, answer in answers.items():
            if answer.get("stance") != "unknown":
                known += 1
                if answer.get("scoreable"):
                    scoreable += 1
                    c = check_by_id.get(f"{party_id}__{statement_id}")
                    if not c or not c.get("quote_found"):
                        raise SourceError(f"unchecked scoreable evidence: {party_id}/{statement_id}")
        counts["scoreable"] += scoreable
        print(f"{party_id:6} known {known:2}/13  scoreable {scoreable:2}/13")
    print("scoreable evidence rows:", counts["scoreable"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true", help="fetch all sources and rewrite manifest")
    parser.add_argument("--force", action="store_true", help="allow replacing an existing manifest")
    parser.add_argument("--check", action="store_true", help="verify cached hashes and quotations")
    parser.add_argument("--report", action="store_true", help="print evidence counts")
    args = parser.parse_args()
    try:
        if args.refresh:
            refresh(force=args.force)
        if args.check:
            check()
        if args.report:
            report()
    except SourceError as error:
        print("ERROR:", error, file=sys.stderr)
        raise SystemExit(1)
    if not (args.refresh or args.check or args.report):
        parser.print_help()


if __name__ == "__main__":
    main()
