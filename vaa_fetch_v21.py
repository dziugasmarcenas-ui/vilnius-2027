#!/usr/bin/env python3
"""Bounded HTTPS retrieval and text normalisation for the v21 evidence pipeline.

This module deliberately keeps raw source text out of the repository.  The
content-addressed cache lives in `.cache/vaa_v21/`; committed manifests retain
URLs, retrieval times, status codes and SHA-256 hashes so a later run can prove
whether the bytes and extracted text are the same.
"""
from __future__ import annotations

import hashlib
import html
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / ".cache" / "vaa_v21"
MAX_BYTES = 20_000_000
USER_AGENT = "ManoMiestas-2027-Evidence/21 (public civic research; contact via repository)"


class FetchError(ValueError):
    pass


def sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript", "svg"):
            self.hidden += 1
        if not self.hidden and tag in (
            "p", "div", "li", "br", "h1", "h2", "h3", "h4", "tr", "article",
            "section", "blockquote", "td", "th",
        ):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript", "svg"):
            self.hidden = max(0, self.hidden - 1)
        if not self.hidden and tag in (
            "p", "div", "li", "h1", "h2", "h3", "h4", "tr", "article",
            "section", "blockquote", "td", "th",
        ):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def extract_html(raw: bytes, encoding: str = "utf-8") -> str:
    text = raw.decode(encoding, errors="replace")
    parser = _TextExtractor()
    parser.feed(text)
    lines = []
    for line in "".join(parser.parts).splitlines():
        line = re.sub(r"\s+", " ", html.unescape(line)).strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def normalise(text: str) -> str:
    """Normalise extracted text for quotation checks, not for display.

    PDF extraction often leaves soft hyphens and hyphenated line breaks.  The
    checker accepts those layout artefacts while still requiring the words to
    occur in the same order.  Curly quotes and dashes are canonicalised.
    """
    text = text.replace("\u00ad", "")
    text = text.replace("\u2019", "'").replace("\u2018", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    text = re.sub(r"-\s*\n\s*", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def quote_present(text: str, quote: str) -> bool:
    haystack = normalise(text).casefold()
    needle = normalise(quote).casefold()
    return bool(needle) and needle in haystack


def _pdf_to_text(raw_path: Path) -> str:
    tool = shutil.which("pdftotext")
    if not tool:
        raise FetchError("pdftotext unavailable; install Poppler to extract PDFs")
    proc = subprocess.run(
        [tool, "-layout", str(raw_path), "-"],
        capture_output=True,
        timeout=45,
        check=False,
    )
    if proc.returncode:
        raise FetchError("pdftotext failed: " + proc.stderr.decode("utf-8", "replace")[:300])
    return proc.stdout.decode("utf-8", "replace")


def fetch(url: str, cache_dir: Path = CACHE, timeout: int = 40) -> dict:
    """Fetch one HTTPS URL and return a manifest row plus extracted text.

    No HTTP fallback, TLS bypass, login, retry or JavaScript rendering is used.
    """
    if not url.startswith("https://"):
        raise FetchError("only https:// URLs are accepted")
    row = {
        "url": url,
        "final_url": url,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "http_status": None,
        "mime_type": None,
        "content_sha256": None,
        "text_sha256": None,
        "extraction_method": "none",
        "extraction_warnings": [],
        "transport": "https",
        "bytes": 0,
    }
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            row["http_status"] = response.status
            row["final_url"] = response.url
            row["mime_type"] = response.headers.get_content_type()
            if not response.url.startswith("https://"):
                raise FetchError("HTTPS redirected to a non-HTTPS URL")
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise FetchError(f"response exceeds {MAX_BYTES} bytes")
            row["content_sha256"] = sha256_bytes(raw)
            row["bytes"] = len(raw)
            cache_dir.mkdir(parents=True, exist_ok=True)
            raw_path = cache_dir / (row["content_sha256"] + ".raw")
            raw_path.write_bytes(raw)
            if row["mime_type"] == "application/pdf" or raw.startswith(b"%PDF"):
                text = _pdf_to_text(raw_path)
                row["extraction_method"] = "pdftotext -layout (Poppler)"
                row["extraction_warnings"].append(
                    "PDF columns and hyphenation can change extraction order; inspect page context"
                )
            elif row["mime_type"] in ("text/html", "application/xhtml+xml"):
                encoding = response.headers.get_content_charset() or "utf-8"
                text = extract_html(raw, encoding)
                row["extraction_method"] = "html.parser-v21"
                row["extraction_warnings"].append(
                    "navigation retained; no JavaScript rendering"
                )
            elif row["mime_type"] == "text/plain":
                text = raw.decode("utf-8", "replace")
                row["extraction_method"] = "utf-8-text-v21"
            else:
                text = ""
                row["extraction_warnings"].append("unsupported MIME type; raw bytes only")
            if not text.strip():
                row["extraction_warnings"].append("no extracted text; not evidence of absence")
                return {**row, "text": "", "raw": raw if "raw" in locals() else b""}
            row["text_sha256"] = sha256_text(text)
            (cache_dir / (row["text_sha256"] + ".txt")).write_text(text, encoding="utf-8")
            return {**row, "text": text, "raw": raw}
    except urllib.error.HTTPError as error:
        row["http_status"] = error.code
        row["extraction_warnings"].append(f"HTTP {error.code}; no bypass or automatic retry")
    except (OSError, ValueError, FetchError, subprocess.SubprocessError) as error:
        row["extraction_warnings"].append(type(error).__name__ + ": " + str(error))
    return {**row, "text": "", "raw": raw if "raw" in locals() else b""}


def read_cached_text(row: dict, cache_dir: Path = CACHE) -> str:
    if not row.get("text_sha256"):
        return ""
    path = cache_dir / (row["text_sha256"] + ".txt")
    if not path.exists():
        raise FetchError("cached text missing for " + row["url"])
    text = path.read_text("utf-8")
    if sha256_text(text) != row["text_sha256"]:
        raise FetchError("cached text hash mismatch for " + row["url"])
    return text


def check_row_hashes(row: dict, cache_dir: Path = CACHE) -> list[str]:
    errors = []
    for suffix, key in ((".raw", "content_sha256"), (".txt", "text_sha256")):
        digest = row.get(key)
        if not digest:
            continue
        path = cache_dir / (digest + suffix)
        if not path.exists():
            errors.append(f"{row.get('url')}: missing {suffix} cache")
        elif sha256_bytes(path.read_bytes()) != digest:
            errors.append(f"{row.get('url')}: {suffix} hash mismatch")
    return errors
