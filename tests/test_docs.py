"""The architecture diagrams must describe the code as it is: same pipeline steps, same gate limits."""
import re
import unittest
from pathlib import Path

from secure_mlops.gate import load_policy
from tests.test_pipeline import compiled

ROOT = Path(__file__).resolve().parents[1]
DOC = (ROOT / "docs/architecture.md").read_text()
DOC_GOV = (ROOT / "docs/governance-and-security.md").read_text()


class ArchitectureDocTests(unittest.TestCase):
    def test_every_pipeline_step_is_drawn(self):
        for task in compiled()["root"]["dag"]["tasks"]:
            self.assertIn(f'subgraph s', DOC)
            self.assertRegex(DOC, rf'subgraph s\d\["{task}"\]', task)

    def test_gate_limits_match_the_committed_policy(self):
        p = load_policy(ROOT / "configs/gate.yaml")
        for text in (f"accuracy ≥ {p.min_accuracy:.2f}", f"macro F1 ≥ {p.min_macro_f1:.2f}",
                     f"recall ≥ {p.min_recall_per_label:.2f}", f"calibration error ≤ {p.max_calibration_error:.2f}",
                     f"p95 latency ≤ {p.max_latency_p95_ms:g} ms", f"drop ≤ {p.max_drop_overall:.2f}",
                     f"recall<br/>drop ≤ {p.max_drop_per_label:.2f}", f"≥ {p.min_slice_size} tickets"):
            self.assertIn(text, DOC)

    def test_diagrams_are_closed_mermaid_blocks(self):
        for doc in (DOC, (ROOT / "README.md").read_text()):
            self.assertEqual(doc.count("```mermaid"), len(re.findall(r"```mermaid\n.*?\n```", doc, re.S)))


class SecurityPolicyTests(unittest.TestCase):
    def test_policy_asks_for_private_reports_and_links_the_threat_model(self):
        text = (ROOT / "SECURITY.md").read_text()
        self.assertIn("Report a vulnerability", text)
        self.assertIn("not in a public issue", text)
        self.assertIn("docs/governance-and-security.md#4-threat-model", text)
        self.assertIn("## 4. Threat model", DOC_GOV)


class DrillOutputDocTests(unittest.TestCase):
    def test_the_documented_runs_show_every_check_the_cluster_scripts_make(self):
        import re
        documented = (ROOT / "docs/serving.md").read_text() + (ROOT / "docs/monitoring.md").read_text()
        for script in ("drill-admission.sh", "check-monitoring.sh", "drill-gitops.sh"):
            text = (ROOT / "scripts" / script).read_text()
            for line in re.findall(r'echo "(PASS: [^"$]+)', text):
                with self.subTest(script=script, check=line[:50]):
                    self.assertIn(line.split(" ${")[0].split(" $(")[0].rstrip(" .,"), documented)
