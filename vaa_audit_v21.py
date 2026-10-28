#!/usr/bin/env python3
"""Static HTML audit for the built pages.

This is a deterministic backstop, not a substitute for a screen reader or a
human accessibility review.  It checks the structural problems that are easy
to introduce and cheap to detect:

  - exactly one h1, no skipped heading levels;
  - unique ids, existing fragment targets;
  - lang, title, viewport, description and a main landmark;
  - labelled links, buttons, inputs, selects and textareas;
  - alt attributes on images and a header row on tables;
  - no inline event handlers (the CSP would block them anyway);
  - exactly one aria-current="page" in the primary navigation.

Usage:
  python3 vaa_audit_v21.py
  python3 vaa_audit_v21.py --report .cache/vaa_v21/audit.json
"""
from __future__ import annotations

import argparse
import json
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
DEFAULT_REPORT = ROOT / ".cache" / "vaa_v21" / "audit_v21.json"
FORMAT = "vaa-html-audit-v1"

VOID = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
FORM_CONTROLS = {"input", "select", "textarea"}


class Node:
    __slots__ = ("tag", "attrs", "children", "line", "parent", "text")

    def __init__(
        self,
        tag: str | None,
        attrs: dict[str, str] | None = None,
        line: int = 0,
        parent: "Node | None" = None,
        text: str = "",
    ) -> None:
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list[Node] = []
        self.line = line
        self.parent = parent
        self.text = text

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def content_text(self) -> str:
        if self.tag is None:
            return self.text
        parts = []
        for child in self.children:
            if child.tag == "img":
                parts.append(child.attrs.get("alt", ""))
            else:
                parts.append(child.content_text())
        return " ".join(part for part in parts if part).strip()


class TreeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node("document")
        self.current = self.root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(
            tag,
            {key: value or "" for key, value in attrs},
            line=self.getpos()[0],
            parent=self.current,
        )
        self.current.children.append(node)
        if tag not in VOID:
            self.current = node

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = Node(
            tag,
            {key: value or "" for key, value in attrs},
            line=self.getpos()[0],
            parent=self.current,
        )
        self.current.children.append(node)

    def handle_endtag(self, tag: str) -> None:
        node = self.current
        while node is not self.root:
            if node.tag == tag:
                self.current = node.parent or self.root
                return
            node = node.parent or self.root

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.current.children.append(
                Node(None, line=self.getpos()[0], parent=self.current, text=data.strip())
            )


def parse_html(text: str) -> Node:
    parser = TreeParser()
    parser.feed(text)
    parser.close()
    return parser.root


def _issue(level: str, code: str, message: str, line: int = 0) -> dict[str, Any]:
    return {"level": level, "code": code, "message": message, "line": line}


def _has_accessible_name(node: Node, label_for: set[str]) -> bool:
    if node.attrs.get("aria-label", "").strip():
        return True
    if node.attrs.get("aria-labelledby", "").strip():
        return True
    if node.attrs.get("title", "").strip():
        return True
    if node.content_text().strip():
        return True
    if node.tag in FORM_CONTROLS:
        control_id = node.attrs.get("id")
        if control_id and control_id in label_for:
            return True
        parent = node.parent
        while parent is not None and parent.tag != "document":
            if parent.tag == "label":
                return True
            parent = parent.parent
    return False


def audit_html(text: str, name: str = "page.html") -> dict[str, Any]:
    root = parse_html(text)
    issues: list[dict[str, Any]] = []
    nodes = list(root.walk())
    by_tag: dict[str, list[Node]] = {}
    for node in nodes:
        if node.tag:
            by_tag.setdefault(node.tag, []).append(node)

    # Document basics.
    html = by_tag.get("html", [None])[0]
    if html is None or not html.attrs.get("lang", "").strip():
        issues.append(_issue("error", "html-lang", "html element needs a lang attribute"))
    for tag in ("title",):
        if tag not in by_tag:
            issues.append(_issue("error", "title", "missing <title>"))
        elif not by_tag[tag][0].content_text().strip():
            issues.append(_issue("error", "title-empty", "empty <title>"))
    metas = {
        node.attrs.get("name", "").lower(): node for node in by_tag.get("meta", [])
    }
    if not metas.get("description", Node("x")).attrs.get("content", "").strip():
        issues.append(_issue("error", "description", "missing meta description"))
    viewport = metas.get("viewport", Node("x")).attrs.get("content", "")
    if "width=device-width" not in viewport:
        issues.append(_issue("error", "viewport", "viewport must include width=device-width"))
    if not any(node.attrs.get("charset") for node in by_tag.get("meta", [])):
        issues.append(_issue("warning", "charset", "no explicit charset meta"))

    # Landmarks.
    mains = by_tag.get("main", [])
    if len(mains) != 1:
        issues.append(_issue("error", "main", f"expected one <main>, found {len(mains)}"))
    for nav in by_tag.get("nav", []):
        if not (
            nav.attrs.get("aria-label", "").strip()
            or nav.attrs.get("aria-labelledby", "").strip()
        ):
            issues.append(_issue("error", "nav-label", "nav needs an accessible name", nav.line))
    current = [node for node in by_tag.get("a", []) if node.attrs.get("aria-current") == "page"]
    if len(current) > 1:
        issues.append(
            _issue(
                "error",
                "aria-current",
                f'expected at most one aria-current="page", found {len(current)}',
            )
        )
    elif not current:
        issues.append(
            _issue("warning", "aria-current", 'no aria-current="page" (page not in primary nav)')
        )

    # Skip link.
    skip = [
        node
        for node in by_tag.get("a", [])
        if node.attrs.get("href", "").startswith("#")
        and node.content_text().strip()
    ]
    if not skip:
        issues.append(_issue("warning", "skip-link", "no in-page skip link"))

    # Ids and fragments.
    ids: dict[str, int] = {}
    for node in nodes:
        value = node.attrs.get("id", "")
        if not value:
            continue
        if value in ids:
            issues.append(
                _issue("error", "duplicate-id", f"duplicate id {value!r}", node.line)
            )
        else:
            ids[value] = node.line
    for node in by_tag.get("a", []):
        href = node.attrs.get("href", "")
        if href.startswith("#") and len(href) > 1 and href[1:] not in ids:
            issues.append(
                _issue("error", "missing-fragment", f"no element with id {href[1:]!r}", node.line)
            )

    # Headings.
    headings = [node for node in nodes if node.tag in HEADINGS]
    if len(by_tag.get("h1", [])) != 1:
        issues.append(
            _issue("error", "h1-count", f"expected one h1, found {len(by_tag.get('h1', []))}")
        )
    previous = 0
    for node in headings:
        level = int(node.tag[1])
        if previous and level > previous + 1:
            issues.append(
                _issue(
                    "error",
                    "heading-jump",
                    f"{node.tag} after h{previous} skips a level",
                    node.line,
                )
            )
        if not node.content_text().strip():
            issues.append(_issue("error", "empty-heading", f"empty {node.tag}", node.line))
        previous = level

    # Images.
    for node in by_tag.get("img", []):
        if "alt" not in node.attrs:
            issues.append(_issue("error", "img-alt", "img needs an alt attribute", node.line))

    # Links and buttons.
    for tag in ("a", "button"):
        for node in by_tag.get(tag, []):
            if not _has_accessible_name(node, set()):
                issues.append(
                    _issue("error", "control-name", f"{tag} has no accessible name", node.line)
                )

    # Form controls.
    label_for = {
        node.attrs.get("for", "") for node in by_tag.get("label", []) if node.attrs.get("for")
    }
    for node in (node for tag in FORM_CONTROLS for node in by_tag.get(tag, [])):
        if node.attrs.get("type", "").lower() == "hidden":
            continue
        if not _has_accessible_name(node, label_for):
            issues.append(
                _issue("error", "control-label", f"{node.tag} has no label", node.line)
            )

    # Tables.
    for node in by_tag.get("table", []):
        if not any(child.tag == "th" for child in node.walk()):
            issues.append(_issue("error", "table-header", "table has no th cells", node.line))
        has_caption = any(child.tag == "caption" for child in node.children)
        if not has_caption and not (
            node.attrs.get("aria-label", "").strip()
            or node.attrs.get("aria-labelledby", "").strip()
        ):
            issues.append(
                _issue("warning", "table-caption", "table has no caption or label", node.line)
            )

    # Inline handlers would be blocked by the CSP.
    for node in nodes:
        for key in node.attrs:
            if key.lower().startswith("on"):
                issues.append(
                    _issue("error", "inline-handler", f"inline handler {key}", node.line)
                )

    errors = [row for row in issues if row["level"] == "error"]
    return {
        "page": name,
        "ok": not errors,
        "errors": len(errors),
        "warnings": len(issues) - len(errors),
        "issues": issues,
    }


def audit_dist(dist: Path = DIST) -> dict[str, Any]:
    pages = sorted(page for page in dist.glob("*.html") if not page.name.startswith("_"))
    if not pages:
        raise ValueError(f"no HTML pages in {dist}")
    results = [audit_html(page.read_text("utf-8"), page.name) for page in pages]
    return {
        "format": FORMAT,
        "pages": results,
        "errors": sum(page["errors"] for page in results),
        "warnings": sum(page["warnings"] for page in results),
        "ok": all(page["ok"] for page in results),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=DIST)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)
    report = audit_dist(args.dist)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"{len(report['pages'])} pages: {report['errors']} errors, {report['warnings']} warnings")
    for page in report["pages"]:
        for row in page["issues"]:
            if row["level"] == "error":
                print(f"ERROR {page['page']}:{row['line']} {row['code']} — {row['message']}")
            elif row["code"] in ("table-caption", "skip-link", "charset"):
                print(f"WARN  {page['page']}:{row['line']} {row['code']} — {row['message']}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
