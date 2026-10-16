"""The README's diagrams are generated: the committed SVGs must match the generator and describe the real pipeline."""
import importlib.util
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from tests.test_pipeline import compiled

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("architecture_diagram", ROOT / "scripts/architecture_diagram.py")
diagram = importlib.util.module_from_spec(spec)
sys.modules["architecture_diagram"] = diagram
spec.loader.exec_module(diagram)


class DiagramTests(unittest.TestCase):
    def test_committed_svgs_match_the_generator(self):
        with tempfile.TemporaryDirectory() as d:
            for fresh in diagram.write(Path(d)):
                committed = ROOT / "docs/images" / fresh.name
                self.assertEqual(committed.read_text(), fresh.read_text(),
                                 f"{committed.name} is stale: run python scripts/architecture_diagram.py")

    def test_valid_accessible_svg_in_both_themes(self):
        for name in diagram.DIAGRAMS:
            for theme in diagram.THEMES:
                root = ET.fromstring((ROOT / f"docs/images/{name}-{theme}.svg").read_text())
                self.assertEqual(root.get("role"), "img")
                ns = "{http://www.w3.org/2000/svg}"
                self.assertTrue(root.find(f"{ns}title").text and root.find(f"{ns}desc").text)

    def test_dag_shows_the_pipelines_real_tasks_and_edges(self):
        tasks = compiled()["root"]["dag"]["tasks"]
        self.assertEqual({t for t, _, _ in diagram.TASKS}, set(tasks))
        drawn = {(a, b) for a, b, _ in diagram.EDGES}
        real = {(dep, t) for t, spec in tasks.items() for dep in spec.get("dependentTasks", [])}
        self.assertEqual(drawn, real)

    def test_readme_shows_both_themes(self):
        readme = (ROOT / "README.md").read_text()
        for name in diagram.DIAGRAMS:
            self.assertIn(f'srcset="docs/images/{name}-dark.svg"', readme)
            self.assertIn(f'src="docs/images/{name}-light.svg"', readme)
