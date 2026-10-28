#!/usr/bin/env python3
"""Attach a readable source-context snippet to every quoted evidence row.

The snippet is generated from the cached, hash-verified source text.  It is
stored in the curated dataset so the public build does not need `.cache/`, and
it lets a reader see the passage around a quotation instead of only the
quotation itself.

Usage:
  python3 vaa_context_v21.py --write
  python3 vaa_context_v21.py --check
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from vaa_fetch_v21 import CACHE, normalise, read_cached_text

ROOT = Path(__file__).resolve().parent
DATASET = ROOT / "vaa" / "vilnius_2027_v21.json"
MANIFEST = ROOT / "vaa" / "review" / "source_manifest_v21.json"
RADIUS = 340


class ContextError(ValueError):
    pass


def build_context(text: str, quote: str, radius: int = RADIUS) -> str | None:
    haystack = normalise(text)
    needle = normalise(quote)
    if not needle:
        return None
    index = haystack.find(needle)
    if index < 0:
        return None
    start = max(0, index - radius)
    end = min(len(haystack), index + len(needle) + radius)
    if start > 0:
        space = haystack.rfind(" ", 0, start)
        start = space + 1 if space >= 0 else 0
    if end < len(haystack):
        space = haystack.find(" ", end)
        end = space if space >= 0 else len(haystack)
    snippet = haystack[start:end].strip()
    prefix = "… " if start > 0 else ""
    suffix = " …" if end < len(haystack) else ""
    return prefix + snippet + suffix


def attach(doc: dict[str, Any], manifest: dict[str, Any]) -> int:
    by_id = {row["source_id"]: row for row in manifest["sources"]}
    changed = 0
    for party_id, rows in doc["answers"].items():
        for statement_id, answer in rows.items():
            quote = answer.get("quote")
            if not quote:
                continue
            source_id = answer.get("source_id")
            row = by_id.get(source_id)
            if not row:
                raise ContextError(f"{party_id}/{statement_id}: source not in manifest")
            text = read_cached_text(row, CACHE)
            context = build_context(text, quote)
            if not context:
                raise ContextError(f"{party_id}/{statement_id}: quote not found in cached text")
            if answer.get("source_context") != context:
                answer["source_context"] = context
                changed += 1
    return changed


def check(doc: dict[str, Any]) -> int:
    checked = 0
    for party_id, rows in doc["answers"].items():
        for statement_id, answer in rows.items():
            if not answer.get("quote"):
                continue
            context = answer.get("source_context")
            if not isinstance(context, str) or not context.strip():
                raise ContextError(f"{party_id}/{statement_id}: source_context missing")
            if normalise(answer["quote"]).casefold() not in normalise(context).casefold():
                raise ContextError(f"{party_id}/{statement_id}: context does not contain quote")
            if len(context) > 1600:
                raise ContextError(f"{party_id}/{statement_id}: context too long")
            checked += 1
    return checked


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="write source_context into the dataset")
    parser.add_argument("--check", action="store_true", help="verify every quoted row has a valid context")
    args = parser.parse_args()
    try:
        doc = json.loads(DATASET.read_text("utf-8"))
        if args.write:
            manifest = json.loads(MANIFEST.read_text("utf-8"))
            changed = attach(doc, manifest)
            DATASET.write_text(
                json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(f"OK: {changed} context snippets written")
        if args.check:
            checked = check(doc)
            print(f"OK: {checked} quoted rows have source context containing the quote")
    except (ContextError, OSError, KeyError) as error:
        print("ERROR:", error, file=sys.stderr)
        raise SystemExit(1)
    if not (args.write or args.check):
        parser.print_help()


if __name__ == "__main__":
    main()
