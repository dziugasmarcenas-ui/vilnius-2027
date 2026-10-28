"""Release-hardening tests: source watch, link audit, HTML audit, importer fuzz."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

import vaa_audit_v21  # noqa: E402
import vaa_links_v21  # noqa: E402
import vaa_watch_v21  # noqa: E402
from vaa_register_v21 import RegisterError, parse_index, validate_register  # noqa: E402


VALID_PAGE = """<!doctype html>
<html lang="lt">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="Testas">
<title>Testas</title>
</head>
<body>
<a class="skip" href="#main">Pereiti prie turinio</a>
<nav aria-label="Pagrindinė navigacija"><a href="index.html" aria-current="page">Pradžia</a></nav>
<main id="main">
<h1>Antraštė</h1>
<h2>Skyrius</h2>
<img src="x.png" alt="">
<table><caption>Lentelė</caption><thead><tr><th>A</th></tr></thead><tbody><tr><td>1</td></tr></tbody></table>
<label for="q">Klausimas</label><input id="q" type="text">
<a href="#main">Į viršų</a>
</main>
</body>
</html>
"""


class WatchTest(unittest.TestCase):
    def row(self, **overrides):
        row = {
            "source_id": "s1",
            "url": "https://example.test/a",
            "label": "A",
            "content_sha256": "c1",
            "text_sha256": "t1",
        }
        row.update(overrides)
        return row

    def observed(self, **overrides):
        row = {"http_status": 200, "content_sha256": "c1", "text_sha256": "t1"}
        row.update(overrides)
        return row

    def test_unchanged(self):
        self.assertEqual(
            vaa_watch_v21.classify(self.row(), self.observed()), vaa_watch_v21.UNCHANGED
        )

    def test_byte_only_change_is_not_a_text_change(self):
        state = vaa_watch_v21.classify(
            self.row(), self.observed(content_sha256="c2")
        )
        self.assertEqual(state, vaa_watch_v21.CONTENT_ONLY)
        self.assertNotIn(state, vaa_watch_v21.CHANGED_STATES)

    def test_text_change_is_a_failure(self):
        state = vaa_watch_v21.classify(self.row(), self.observed(text_sha256="t2"))
        self.assertEqual(state, vaa_watch_v21.TEXT_CHANGED)
        self.assertIn(state, vaa_watch_v21.CHANGED_STATES)

    def test_missing_text_is_a_change_not_a_pass(self):
        self.assertEqual(
            vaa_watch_v21.classify(self.row(), self.observed(text_sha256=None)),
            vaa_watch_v21.TEXT_CHANGED,
        )

    def test_http_error_and_unreachable(self):
        self.assertEqual(
            vaa_watch_v21.classify(self.row(), self.observed(http_status=503)),
            vaa_watch_v21.BAD_STATUS,
        )
        self.assertEqual(
            vaa_watch_v21.classify(self.row(), {"error": "timeout"}),
            vaa_watch_v21.UNREACHABLE,
        )

    def test_compare_flags_a_source_that_was_not_fetched(self):
        rows = vaa_watch_v21.compare([self.row()], [])
        self.assertEqual(rows[0]["state"], vaa_watch_v21.UNREACHABLE)
        self.assertFalse(rows[0]["ok"])

    def test_watch_uses_the_injected_fetch_and_writes_a_report(self):
        manifest = {
            "format": "vaa-source-manifest-v21",
            "generated_at": "2026-10-28",
            "sources": [self.row()],
        }
        with tempfile.TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "report.json"
            with mock.patch.object(
                vaa_watch_v21, "fetch", return_value=self.observed()
            ):
                report = vaa_watch_v21.watch(manifest, cache_dir=Path(tmp))
            vaa_watch_v21.write_report(report, report_path)
            saved = json.loads(report_path.read_text("utf-8"))
        self.assertEqual(saved["format"], vaa_watch_v21.FORMAT)
        self.assertEqual(saved["counts"]["unchanged"], 1)
        self.assertEqual(saved["changed"], [])


class LinkAuditTest(unittest.TestCase):
    def build_site(self, body):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        dist = Path(tmp.name)
        (dist / "index.html").write_text(VALID_PAGE, "utf-8")
        (dist / "other.html").write_text(
            VALID_PAGE.replace('href="index.html"', 'href="index.html#main"'), "utf-8"
        )
        (dist / "data").mkdir()
        (dist / "data" / "x.json").write_text("{}", "utf-8")
        (dist / "x.png").write_bytes(b"")
        (dist / "page.html").write_text(body, "utf-8")
        return dist

    def test_valid_links_pass(self):
        dist = self.build_site(
            """<html><body>
            <a href="index.html">A</a>
            <a href="other.html#main">B</a>
            <a href="index.html#main">C</a>
            <a href="data/x.json">D</a>
            <a href="https://example.test/x">E</a>
            </body></html>"""
        )
        report = vaa_links_v21.audit(dist)
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(report["counts"]["external"], 1)

    def test_local_failures_are_errors(self):
        dist = self.build_site(
            """<html><body>
            <a href="missing.html">A</a>
            <a href="other.html#nope">B</a>
            <a href="../escape.html">C</a>
            <a href="fixtures/raw.html">D</a>
            <a href="http://example.test/">E</a>
            <a href="javascript:alert(1)">F</a>
            <a href="">G</a>
            </body></html>"""
        )
        report = vaa_links_v21.audit(dist)
        self.assertFalse(report["ok"])
        reasons = " ".join(row["reason"] for row in report["errors"])
        for needle in ("missing local file", "missing fragment", "escapes dist", "private", "plain HTTP", "forbidden scheme", "empty link"):
            self.assertIn(needle, reasons)

    def test_directory_link_resolves_to_index(self):
        dist = self.build_site('<html><body><a href="data/">D</a></body></html>')
        (dist / "data" / "index.html").write_text(VALID_PAGE, "utf-8")
        report = vaa_links_v21.audit(dist)
        self.assertTrue(report["ok"], report["errors"])


class HtmlAuditTest(unittest.TestCase):
    def codes(self, html):
        result = vaa_audit_v21.audit_html(html)
        return result, {row["code"] for row in result["issues"]}

    def test_valid_page_passes(self):
        result, _ = self.codes(VALID_PAGE)
        self.assertTrue(result["ok"], result["issues"])

    def test_duplicate_ids_and_heading_jumps(self):
        html = VALID_PAGE.replace("<h2>Skyrius</h2>", "<h2>Skyrius</h2><h4>Per šokį</h4>")
        html = html.replace('<main id="main">', '<main id="main"><div id="main"></div>')
        result, codes = self.codes(html)
        self.assertFalse(result["ok"])
        self.assertIn("duplicate-id", codes)
        self.assertIn("heading-jump", codes)

    def test_unlabelled_control_and_inline_handler(self):
        html = VALID_PAGE.replace(
            '<label for="q">Klausimas</label><input id="q" type="text">',
            '<input id="q2" type="text" onclick="x()">',
        )
        result, codes = self.codes(html)
        self.assertFalse(result["ok"])
        self.assertIn("control-label", codes)
        self.assertIn("inline-handler", codes)

    def test_table_without_header_is_an_error(self):
        html = VALID_PAGE.replace(
            "<table><caption>Lentelė</caption><thead><tr><th>A</th></tr></thead><tbody><tr><td>1</td></tr></tbody></table>",
            "<table><tbody><tr><td>1</td></tr></tbody></table>",
        )
        result, codes = self.codes(html)
        self.assertFalse(result["ok"])
        self.assertIn("table-header", codes)
        self.assertIn("table-caption", codes)

    def test_built_site_passes(self):
        if not (ROOT / "dist").is_dir():
            self.skipTest("dist not built")
        report = vaa_audit_v21.audit_dist(ROOT / "dist")
        self.assertTrue(report["ok"], [p for p in report["pages"] if not p["ok"]])


class ImporterFuzzTest(unittest.TestCase):
    def test_empty_page_yields_no_participants(self):
        parsed = parse_index("")
        self.assertEqual(parsed["participants_count"], 0)
        self.assertEqual(parsed["vilnius_lists"], [])

    def test_truncated_html_does_not_crash(self):
        for source in ("<b>1. Partija", "<b>1. Partija X</b><br/>Partija", "<a href='x'>"):
            parsed = parse_index(source)
            self.assertIsInstance(parsed, dict)

    def test_broken_participant_link_raises(self):
        source = (
            "<b>1. Partija X</b><br/>Partija<br/>"
            "<a href='no-src-url'>Vilniaus miesto rinkimų apygardoje Nr. 57 kandidatų sąrašas</a>"
        )
        with self.assertRaises(RegisterError):
            parse_index(source)

    def test_empty_register_is_rejected_by_validation(self):
        with self.assertRaises(RegisterError):
            validate_register({"format": "vaa-vilnius-2027-register-v2"})


if __name__ == "__main__":
    unittest.main()
