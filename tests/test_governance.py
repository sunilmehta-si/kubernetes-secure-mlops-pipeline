"""The governance and security page must not claim more than the code does."""
import importlib.util
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("governance_diagrams", ROOT / "scripts/governance_diagrams.py")
gov = importlib.util.module_from_spec(spec)
sys.modules["governance_diagrams"] = gov
spec.loader.exec_module(gov)
DOC = (ROOT / "docs/governance-and-security.md").read_text()


class GovernanceTests(unittest.TestCase):
    def test_built_and_partial_items_name_files_that_exist(self):
        items = [(g[0], g[7], g[8]) for g in gov.GATES] + [(t[0], t[5], t[6]) for t in gov.THREATS] + \
                [(r[0], r[3], r[4]) for r in gov.TRAIL]
        for name, status, path in items:
            with self.subTest(item=name):
                self.assertIn(status, gov.STATUS_LABEL)
                if status in ("built", "partial"):
                    self.assertTrue(path and (ROOT / path).exists(), f"{name} is {status} but names no real file")
                else:
                    self.assertEqual(path, "", f"{name} is planned but names a file")

    def test_doc_tables_match_the_diagram_statuses(self):
        for gid, title, *_, status, _f in gov.GATES:
            row = next(line for line in DOC.splitlines() if line.startswith(f"| {gid} {title}"))
            self.assertIn(status, row.lower(), gid)
        for tid, threat, _w, _c, owasp, status, _f in gov.THREATS:
            row = next(line for line in DOC.splitlines() if line.startswith(f"| {tid} | {threat}"))
            self.assertTrue(row.rstrip(" |").lower().endswith(status), tid)
            self.assertIn(f"| {owasp} |", row, tid)

    def test_committed_svgs_match_the_generator_and_are_accessible(self):
        with tempfile.TemporaryDirectory() as d:
            for fresh in gov.write(Path(d)):
                committed = ROOT / "docs/images" / fresh.name
                self.assertEqual(committed.read_text(), fresh.read_text(),
                                 f"{committed.name} is stale: run python scripts/governance_diagrams.py")
                root = ET.fromstring(committed.read_text())
                self.assertEqual(root.get("role"), "img")

    def test_doc_shows_every_diagram_in_both_themes(self):
        for name in gov.DIAGRAMS:
            self.assertIn(f'srcset="images/{name}-dark.svg"', DOC)
            self.assertIn(f'src="images/{name}-light.svg"', DOC)
