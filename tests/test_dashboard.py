import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("grafana_dashboard", ROOT / "scripts/grafana_dashboard.py")
grafana = importlib.util.module_from_spec(spec)
sys.modules["grafana_dashboard"] = grafana
spec.loader.exec_module(grafana)

from secure_mlops.drift import load_policy  # noqa: E402

COMMITTED = ROOT / "deploy/monitoring/dashboards/ticket-router.json"


class DashboardTests(unittest.TestCase):
    def test_committed_dashboard_matches_the_script(self):
        self.assertEqual(COMMITTED.read_text(), grafana.render(),
                         "ticket-router.json is stale: run python scripts/grafana_dashboard.py")

    def test_every_metric_a_panel_reads_is_exported_by_the_server(self):
        exported = set(re.findall(r'"(smlops_[a-z_]+)"', (ROOT / "secure_mlops/serve/app.py").read_text()))
        board = json.loads(COMMITTED.read_text())
        for panel in board["panels"]:
            for target in panel.get("targets", []):
                for metric in re.findall(r"smlops_[a-z_]+", target["expr"]):
                    self.assertIn(re.sub(r"_(total|bucket|count|sum)$", "", metric), exported, panel["title"])

    def test_drift_panels_draw_the_alert_thresholds(self):
        policy = load_policy(ROOT / "configs/drift.yaml")
        panels = {p["title"]: p for p in json.loads(COMMITTED.read_text())["panels"]}

        def steps(title):
            return [s["value"] for s in panels[title]["fieldConfig"]["defaults"]["thresholds"]["steps"]][1:]
        self.assertEqual(steps("Predicted team mix vs training (PSI)"), [policy.psi_warn, policy.psi_alert])
        self.assertEqual(steps("Unknown words vs the model's baseline"),
                         [policy.live_new_word_ratio_warn, policy.live_new_word_ratio_alert])

    def test_panels_are_unique_and_use_the_chosen_datasource(self):
        board = json.loads(COMMITTED.read_text())
        ids = [p["id"] for p in board["panels"]]
        self.assertEqual(len(ids), len(set(ids)))
        for panel in board["panels"]:
            if panel["type"] != "row":
                self.assertEqual(panel["datasource"]["uid"], "${datasource}", panel["title"])


if __name__ == "__main__":
    unittest.main()
