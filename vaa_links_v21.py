#!/usr/bin/env python3
"""Audit every link in the built static site.

Checks, without a network by default:
  - local targets exist in dist/ (a directory resolves to its index.html);
  - fragment targets exist as ``id`` attributes in the linked page;
  - external links use HTTPS, never plain HTTP or javascript:;
  - no empty href and no link to a private research path.

``--online`` additionally requests each external URL.  External failures are
reported as warnings because many public sites block automated clients; local
failures are always errors.

Usage:
  python3 vaa_links_v21.py
  python3 vaa_links_v21.py --online --report .cache/vaa_v21/links.json
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
DEFAULT_REPORT = ROOT / ".cache" / "vaa_v21" / "links_v21.json"
FORMAT = "vaa-link-audit-v1"
USER_AGENT = "ManoMiestas-link-audit/1.0 (+https://dziugasmarcenas-ui.github.io/vilnius-2027/)"

LINK_ATTRS = {
    "a": "href",
    "link": "href",
    "script": "src",
    "img": "src",
    "source": "src",
    "iframe": "src",
}
PRIVATE_MARKERS = ("fixtures/", ".cache/", "/Users/", "palace.yaml", "docs/superpowers")


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, Any]] = []
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(str(values["id"]))
        attr = LINK_ATTRS.get(tag)
        if attr and attr in values:
            self.links.append(
                {
                    "tag": tag,
                    "attr": attr,
                    "url": (values.get(attr) or "").strip(),
                    "line": self.getpos()[0],
                }
            )


def parse_page(path: Path) -> LinkParser:
    parser = LinkParser()
    parser.feed(path.read_text("utf-8"))
    parser.close()
    return parser


def local_target(source: Path, url: str, dist: Path) -> Path | None:
    """Resolve a relative URL to a file inside dist, or None when it escapes."""
    path = url.split("#", 1)[0].split("?", 1)[0]
    if not path:
        return source
    if path.startswith("/"):
        candidate = (dist / path.lstrip("/")).resolve()
    else:
        candidate = (source.parent / path).resolve()
    try:
        candidate.relative_to(dist.resolve())
    except ValueError:
        return None
    if candidate.is_dir():
        candidate = candidate / "index.html"
    return candidate


def audit(
    dist: Path = DIST,
    online: bool = False,
    timeout: int = 20,
) -> dict[str, Any]:
    pages = sorted(dist.glob("*.html"))
    if not pages:
        raise ValueError(f"no HTML pages in {dist}")
    parsed = {page: parse_page(page) for page in pages}
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    external: dict[str, dict[str, Any]] = {}
    counts = {"pages": len(pages), "links": 0, "local": 0, "external": 0, "fragments": 0}

    for page, parser in parsed.items():
        name = page.name
        for link in parser.links:
            counts["links"] += 1
            url = link["url"]
            where = {"page": name, "line": link["line"], "url": url, "tag": link["tag"]}
            if not url:
                errors.append({**where, "reason": "empty link target"})
                continue
            if any(marker in url for marker in PRIVATE_MARKERS):
                errors.append({**where, "reason": "private research path"})
                continue
            parts = urlsplit(url)
            if parts.scheme in ("mailto", "tel"):
                continue
            if parts.scheme in ("http",):
                errors.append({**where, "reason": "plain HTTP link"})
                continue
            if parts.scheme in ("javascript", "data", "file"):
                errors.append({**where, "reason": f"forbidden scheme {parts.scheme}"})
                continue
            if parts.scheme == "https":
                counts["external"] += 1
                external.setdefault(url, {"url": url, "pages": [], "status": None, "ok": None})
                external[url]["pages"].append(name)
                continue
            if parts.scheme:
                warnings.append({**where, "reason": f"unknown scheme {parts.scheme}"})
                continue
            counts["local"] += 1
            target = local_target(page, url, dist)
            if target is None:
                errors.append({**where, "reason": "link escapes dist/"})
                continue
            if not target.is_file():
                errors.append({**where, "reason": f"missing local file {target.name}"})
                continue
            fragment = parts.fragment
            if fragment:
                counts["fragments"] += 1
                if target.suffix != ".html":
                    warnings.append({**where, "reason": "fragment on a non-HTML file"})
                else:
                    ids = parsed[target].ids if target in parsed else parse_page(target).ids
                    if fragment not in ids:
                        errors.append(
                            {**where, "reason": f"missing fragment #{fragment} in {target.name}"}
                        )

    if online:
        for url, row in external.items():
            row.update(check_external(url, timeout))
            if not row["ok"]:
                warnings.append(
                    {
                        "page": row["pages"][0],
                        "line": 0,
                        "url": url,
                        "tag": "a",
                        "reason": f"external check failed: {row.get('error') or row.get('status')}",
                    }
                )

    return {
        "format": FORMAT,
        "dist": str(dist),
        "online": online,
        "counts": counts,
        "errors": errors,
        "warnings": warnings,
        "external": sorted(external.values(), key=lambda row: row["url"]),
        "ok": not errors,
    }


def check_external(url: str, timeout: int = 20) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": USER_AGENT, "Range": "bytes=0-2048"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            return {
                "status": status,
                "ok": 200 <= status < 400,
                "error": None if 200 <= status < 400 else f"HTTP {status}",
            }
    except urllib.error.HTTPError as error:
        return {"status": error.code, "ok": False, "error": f"HTTP {error.code}"}
    except (urllib.error.URLError, OSError, ValueError) as error:
        return {"status": None, "ok": False, "error": f"{type(error).__name__}: {error}"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=DIST)
    parser.add_argument("--online", action="store_true")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)
    report = audit(args.dist, online=args.online, timeout=args.timeout)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        f"{report['counts']['pages']} pages, {report['counts']['links']} links: "
        f"{len(report['errors'])} errors, {len(report['warnings'])} warnings"
    )
    for row in report["errors"]:
        print(f"ERROR {row['page']}:{row['line']} {row['url']} — {row['reason']}")
    for row in report["warnings"]:
        print(f"WARN  {row['page']}:{row['line']} {row['url']} — {row['reason']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
