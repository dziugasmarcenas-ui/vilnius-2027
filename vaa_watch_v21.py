#!/usr/bin/env python3
"""Watch the curated 2027 sources for silent changes.

The published site cites exact quotations from eleven sources.  A source can
change without anybody editing this repository: a programme gets a new PDF, a
page is re-published, or a register table is refreshed.  This script re-fetches
every URL in ``vaa/review/source_manifest_v21.json`` and compares the observed
content and extracted-text hashes with the recorded ones.

It never edits curated files.  A changed hash is a signal for a human to read
the source again and re-run ``vaa_sources_v21.py --refresh`` deliberately.

Two sources (``e-tar.lt``, ``vrk.lt``) answer a normal network but refuse
GitHub-hosted runners with 403.  Those refusals are reported as
``blocked_from_ci`` and do not fail the run while a recent local check in
``vaa/review/ci_reachability_v21.json`` matches the manifest text hash.  The
excuse expires, and any other status — 404, 500, a timeout — still fails.

Usage:
  python3 vaa_watch_v21.py                 # fetch and report, exit 1 on change
  python3 vaa_watch_v21.py --report out.json
  python3 vaa_watch_v21.py --timeout 60
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from vaa_fetch_v21 import CACHE, ROOT, FetchError, fetch

MANIFEST = ROOT / "vaa" / "review" / "source_manifest_v21.json"
REACHABILITY = ROOT / "vaa" / "review" / "ci_reachability_v21.json"
DEFAULT_REPORT = CACHE / "source_watch_v21.json"
FORMAT = "vaa-source-watch-v1"
REACHABILITY_FORMAT = "vaa-ci-reachability-v21"

UNCHANGED = "unchanged"
CONTENT_ONLY = "content_changed_text_same"
TEXT_CHANGED = "text_changed"
UNREACHABLE = "unreachable"
BAD_STATUS = "http_error"
BLOCKED_FROM_CI = "blocked_from_ci"

# States that mean "the snapshot still matches what a normal reader sees".
OK_STATES = {UNCHANGED, CONTENT_ONLY}
# States a human has to act on.  ``blocked_from_ci`` is reported but does not
# fail the run, because the source is known to refuse datacentre IPs; the
# attestation holding that excuse still expires.
CHANGED_STATES = {TEXT_CHANGED, UNREACHABLE, BAD_STATUS}
DEFAULT_MAX_AGE_DAYS = 45


def load_manifest(path: Path = MANIFEST) -> dict[str, Any]:
    doc = json.loads(path.read_text("utf-8"))
    if doc.get("format") != "vaa-source-manifest-v21":
        raise ValueError("source manifest format invalid")
    rows = doc.get("sources")
    if not isinstance(rows, list) or not rows:
        raise ValueError("source manifest has no sources")
    return doc


def load_reachability(path: Path = REACHABILITY) -> dict[str, Any]:
    """Return ``{"max_age_days": int, "sources": {source_id: entry}}``.

    A missing or malformed file simply means no source is excused.
    """
    empty = {"max_age_days": DEFAULT_MAX_AGE_DAYS, "sources": {}}
    if not path.exists():
        return empty
    try:
        doc = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return empty
    if doc.get("format") != REACHABILITY_FORMAT:
        return empty
    rows = doc.get("sources")
    if not isinstance(rows, list):
        return empty
    max_age = doc.get("max_age_days", DEFAULT_MAX_AGE_DAYS)
    if not isinstance(max_age, int) or max_age <= 0:
        max_age = DEFAULT_MAX_AGE_DAYS
    by_id = {
        row.get("source_id"): row
        for row in rows
        if isinstance(row, dict) and row.get("source_id")
    }
    return {"max_age_days": max_age, "sources": by_id}


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp


def attests_ci_block(
    expected: dict[str, Any],
    observed: dict[str, Any],
    entry: dict[str, Any] | None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    now: datetime | None = None,
) -> bool:
    """True when a refused status is a known CI block backed by a fresh check.

    The excuse needs four things: the observed status is one the source is
    known to refuse, a local read returned 200, that local read matched the
    manifest text hash, and the check is not older than ``max_age_days``.
    Anything else falls through to ``http_error`` and fails the run.
    """
    if not entry:
        return False
    status = observed.get("http_status")
    blocked = entry.get("blocked_statuses") or [403, 429]
    if not isinstance(blocked, list) or status not in blocked:
        return False
    if entry.get("verified_http_status") != 200:
        return False
    if entry.get("verified_text_sha256") != expected.get("text_sha256"):
        return False
    checked = _parse_timestamp(entry.get("verified_locally_at"))
    if checked is None:
        return False
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    age = current - checked
    return timedelta(0) <= age <= timedelta(days=max_age_days)


def classify(
    expected: dict[str, Any],
    observed: dict[str, Any],
    attestation: dict[str, Any] | None = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    now: datetime | None = None,
) -> str:
    """Return a stable state for one source comparison.

    ``content_changed_text_same`` is not a failure: byte-level changes such as
    cache busters or tracking parameters can leave the readable text identical.
    Any text change is a failure because a quotation may no longer exist.
    ``blocked_from_ci`` is reported separately: the source refused this network
    but a recent local check matched the snapshot, so nothing is claimed about
    the current remote text beyond that dated check.
    """
    if observed.get("error"):
        return UNREACHABLE
    status = observed.get("http_status")
    if status != 200:
        if attests_ci_block(expected, observed, attestation, max_age_days, now):
            return BLOCKED_FROM_CI
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
    attestations: dict[str, dict[str, Any]] | None = None,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Pair rows by ``source_id`` and classify each pair."""
    attestations = attestations or {}
    observed_by_id = {row.get("source_id"): row for row in observed_rows}
    result = []
    for expected in expected_rows:
        source_id = expected.get("source_id")
        observed = observed_by_id.get(source_id, {"error": "source not fetched"})
        entry = attestations.get(source_id)
        state = classify(expected, observed, entry, max_age_days, now)
        result.append(
            {
                "source_id": source_id,
                "url": expected.get("url"),
                "label": expected.get("label"),
                "state": state,
                "ok": state in OK_STATES,
                "expected_content_sha256": expected.get("content_sha256"),
                "observed_content_sha256": observed.get("content_sha256"),
                "expected_text_sha256": expected.get("text_sha256"),
                "observed_text_sha256": observed.get("text_sha256"),
                "http_status": observed.get("http_status"),
                "error": observed.get("error"),
                "ci_attestation": (
                    {
                        "verified_locally_at": entry.get("verified_locally_at"),
                        "verified_text_sha256": entry.get("verified_text_sha256"),
                    }
                    if state == BLOCKED_FROM_CI
                    else None
                ),
            }
        )
    return result


def attest_blocked(
    reachability: dict[str, Any],
    manifest: dict[str, Any],
    timeout: int = 40,
    cache_dir: Path = CACHE / "attest",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Re-fetch every attested source and stamp a fresh local check.

    Only a 200 whose extracted text matches the manifest hash is accepted.  A
    mismatch means the source changed, so nothing is written and the caller
    has to read the source before refreshing the snapshot.
    """
    by_id = {row.get("source_id"): row for row in manifest["sources"]}
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    updated = []
    for source_id, entry in reachability.get("sources", {}).items():
        expected = by_id.get(source_id)
        if not expected:
            raise ValueError(f"{source_id}: not in the source manifest")
        fetched = fetch(expected["url"], cache_dir=cache_dir, timeout=timeout)
        status = fetched.get("http_status")
        if status != 200:
            raise ValueError(f"{source_id}: local fetch returned HTTP {status}")
        if fetched.get("text_sha256") != expected.get("text_sha256"):
            raise ValueError(
                f"{source_id}: local text differs from the manifest; read it before refreshing"
            )
        entry["verified_locally_at"] = stamp
        entry["verified_http_status"] = 200
        entry["verified_text_sha256"] = fetched.get("text_sha256")
        updated.append(source_id)
    return {"stamped": updated, "verified_at": stamp}


def watch(
    manifest: dict[str, Any],
    timeout: int = 40,
    cache_dir: Path = CACHE / "watch",
    reachability: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if reachability is None:
        reachability = load_reachability()
    attestations = reachability.get("sources", {})
    max_age_days = reachability.get("max_age_days", DEFAULT_MAX_AGE_DAYS)
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
    results = compare(
        manifest["sources"], observed, attestations, max_age_days, now
    )
    changed = [row for row in results if row["state"] in CHANGED_STATES]
    blocked = [row for row in results if row["state"] == BLOCKED_FROM_CI]
    return {
        "format": FORMAT,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "snapshot": manifest.get("generated_at"),
        "sources": results,
        "counts": {
            "total": len(results),
            "unchanged": sum(1 for row in results if row["state"] == UNCHANGED),
            "content_only": sum(1 for row in results if row["state"] == CONTENT_ONLY),
            "blocked_from_ci": len(blocked),
            "changed": len(changed),
        },
        "changed": [row["source_id"] for row in changed],
        "blocked_from_ci": [row["source_id"] for row in blocked],
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
    parser.add_argument(
        "--reachability",
        type=Path,
        default=REACHABILITY,
        help="dated evidence for sources that refuse this network",
    )
    parser.add_argument(
        "--attest-blocked",
        action="store_true",
        help="re-check the attested sources locally and rewrite their dates",
    )
    args = parser.parse_args(argv)
    manifest = load_manifest()
    if args.attest_blocked:
        reachability = load_reachability(args.reachability)
        if not reachability["sources"]:
            print("No attested sources to re-check.", file=sys.stderr)
            return 2
        try:
            result = attest_blocked(
                reachability, manifest, timeout=args.timeout
            )
        except (FetchError, OSError, ValueError) as error:
            print(f"Attestation failed: {error}", file=sys.stderr)
            return 2
        doc = json.loads(args.reachability.read_text("utf-8"))
        doc["verified_at"] = result["verified_at"]
        for entry in doc.get("sources", []):
            fresh = reachability["sources"].get(entry.get("source_id"))
            if fresh:
                entry.update(
                    {
                        "verified_locally_at": fresh["verified_locally_at"],
                        "verified_http_status": fresh["verified_http_status"],
                        "verified_text_sha256": fresh["verified_text_sha256"],
                    }
                )
        args.reachability.write_text(
            json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        for source_id in result["stamped"]:
            print(f"attested {source_id} at {result['verified_at']}")
        return 0
    reachability = load_reachability(args.reachability)
    report = watch(manifest, timeout=args.timeout, reachability=reachability)
    write_report(report, args.report)
    for row in report["sources"]:
        if row["state"] == BLOCKED_FROM_CI:
            verified = (row.get("ci_attestation") or {}).get("verified_locally_at")
            print(
                f"{'blocked':7} {row['state']:24} {row['source_id']} "
                f"({row['http_status']}; local check {verified})"
            )
            continue
        marker = "ok" if row["ok"] else "CHANGED"
        print(f"{marker:7} {row['state']:24} {row['source_id']}")
    print(
        f"{report['counts']['total']} sources: "
        f"{report['counts']['unchanged']} unchanged, "
        f"{report['counts']['content_only']} byte-only, "
        f"{report['counts']['blocked_from_ci']} blocked from CI, "
        f"{report['counts']['changed']} changed"
    )
    if report["blocked_from_ci"]:
        print(
            "Blocked sources are excused only until their local check expires; "
            "re-run a local watch and update vaa/review/ci_reachability_v21.json.",
            file=sys.stderr,
        )
    if report["changed"]:
        print("Review the changed sources before refreshing any quotation.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
