#!/usr/bin/env python3
"""Watch the curated 2027 sources for silent changes.

The published site cites exact quotations from eleven sources.  A source can
change without anybody editing this repository: a programme gets a new PDF, a
page is re-published, or a register table is refreshed.  This script re-fetches
every URL in ``vaa/review/source_manifest_v21.json`` and compares the observed
content and extracted-text hashes with the recorded ones.

It never edits curated files.  A changed hash is a signal for a human to read
the source again and re-run ``vaa_sources_v21.py --refresh`` deliberately.

Usage:
  python3 vaa_watch_v21.py                 # fetch and report, exit 1 on change
  python3 vaa_watch_v21.py --report out.json
  python3 vaa_watch_v21.py --timeout 60
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from vaa_fetch_v21 import CACHE, ROOT, FetchError, fetch

MANIFEST = ROOT / "vaa" / "review" / "source_manifest_v21.json"
DEFAULT_REPORT = CACHE / "source_watch_v21.json"
FORMAT = "vaa-source-watch-v1"

UNCHANGED = "unchanged"
CONTENT_ONLY = "content_changed_text_same"
TEXT_CHANGED = "text_changed"
UNREACHABLE = "unreachable"
BAD_STATUS = "http_error"

CHANGED_STATES = {TEXT_CHANGED, UNREACHABLE, BAD_STATUS}


def load_manifest(path: Path = MANIFEST) -> dict[str, Any]:
    doc = json.loads(path.read_text("utf-8"))
    if doc.get("format") != "vaa-source-manifest-v21":
        raise ValueError("source manifest format invalid")
    rows = doc.get("sources")
    if not isinstance(rows, list) or not rows:
        raise ValueError("source manifest has no sources")
    return doc


def classify(expected: dict[str, Any], observed: dict[str, Any]) -> str:
    """Return a stable state for one source comparison.

    ``content_changed_text_same`` is not a failure: byte-level changes such as
    cache busters or tracking parameters can leave the readable text identical.
    Any text change is a failure because a quotation may no longer exist.
    """
    if observed.get("error"):
        return UNREACHABLE
    status = observed.get("http_status")
    if status != 200:
        return BAD_STATUS
    if not observed.get("text_sha256"):
        return TEXT_CHANGED
    if observed.get("text_sha256") != expected.get("text_sha256"):
        return TEXT_CHANGED
    if observed.get("content_sha256") != expected.get("content_sha256"):
        return CONTENT_ONLY
    return UNCHANGED


def compare(
    expected_rows: list[dict[str, Any]],
    observed_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Pair rows by ``source_id`` and classify each pair."""
    observed_by_id = {row.get("source_id"): row for row in observed_rows}
    result = []
    for expected in expected_rows:
        source_id = expected.get("source_id")
        observed = observed_by_id.get(source_id, {"error": "source not fetched"})
        state = classify(expected, observed)
        result.append(
            {
                "source_id": source_id,
                "url": expected.get("url"),
                "label": expected.get("label"),
                "state": state,
                "ok": state not in CHANGED_STATES,
                "expected_content_sha256": expected.get("content_sha256"),
                "observed_content_sha256": observed.get("content_sha256"),
                "expected_text_sha256": expected.get("text_sha256"),
                "observed_text_sha256": observed.get("text_sha256"),
                "http_status": observed.get("http_status"),
                "error": observed.get("error"),
            }
        )
    return result


def watch(
    manifest: dict[str, Any],
    timeout: int = 40,
    cache_dir: Path = CACHE / "watch",
) -> dict[str, Any]:
    observed = []
    for row in manifest["sources"]:
        url = row.get("url")
        try:
            fetched = fetch(url, cache_dir=cache_dir, timeout=timeout)
            observed.append({**fetched, "source_id": row.get("source_id")})
        except (FetchError, OSError, ValueError) as error:
            observed.append(
                {
                    "source_id": row.get("source_id"),
                    "url": url,
                    "http_status": None,
                    "error": f"{type(error).__name__}: {error}",
                }
            )
    results = compare(manifest["sources"], observed)
    changed = [row for row in results if not row["ok"]]
    return {
        "format": FORMAT,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "snapshot": manifest.get("generated_at"),
        "sources": results,
        "counts": {
            "total": len(results),
            "unchanged": sum(1 for row in results if row["state"] == UNCHANGED),
            "content_only": sum(1 for row in results if row["state"] == CONTENT_ONLY),
            "changed": len(changed),
        },
        "changed": [row["source_id"] for row in changed],
    }


def write_report(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--timeout", type=int, default=40)
    args = parser.parse_args(argv)
    manifest = load_manifest()
    report = watch(manifest, timeout=args.timeout)
    write_report(report, args.report)
    for row in report["sources"]:
        marker = "ok" if row["ok"] else "CHANGED"
        print(f"{marker:7} {row['state']:24} {row['source_id']}")
    print(
        f"{report['counts']['total']} sources: "
        f"{report['counts']['unchanged']} unchanged, "
        f"{report['counts']['content_only']} byte-only, "
        f"{report['counts']['changed']} changed"
    )
    if report["changed"]:
        print("Review the changed sources before refreshing any quotation.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
