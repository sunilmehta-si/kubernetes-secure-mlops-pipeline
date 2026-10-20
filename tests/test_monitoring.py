import re
import unittest
from pathlib import Path

import yaml

from secure_mlops.drift import load_policy

ROOT = Path(__file__).resolve().parents[1]
ALERTS = yaml.safe_load((ROOT / "deploy/monitoring/alerts.yaml").read_text())
RULES = {r["alert"]: r for g in ALERTS["groups"] for r in g["rules"]}


def threshold(alert: str) -> float:
    return float(re.search(r">\s*([0-9.]+)\s*$", RULES[alert]["expr"].strip()).group(1))


class AlertRuleTests(unittest.TestCase):
    def test_live_drift_alerts_use_the_drift_policy(self):
        policy = load_policy(ROOT / "configs/drift.yaml")
        self.assertEqual(threshold("TicketRouterTeamMixShifting"), policy.psi_warn)
        self.assertEqual(threshold("TicketRouterTeamMixDrifted"), policy.psi_alert)
        self.assertEqual(threshold("TicketRouterNewVocabularyRising"), policy.live_new_word_ratio_warn)
        self.assertEqual(threshold("TicketRouterNewVocabulary"), policy.live_new_word_ratio_alert)

    def test_only_critical_drift_starts_retraining(self):
        retrain = sorted(name for name, r in RULES.items() if r["labels"].get("action") == "retrain")
        self.assertEqual(retrain, ["TicketRouterNewVocabulary", "TicketRouterTeamMixDrifted"])
        self.assertTrue(all(RULES[name]["labels"]["severity"] == "critical" for name in retrain))

    def test_every_metric_an_alert_reads_is_exported_by_the_server(self):
        source = (ROOT / "secure_mlops/serve/app.py").read_text()
        exported = set(re.findall(r'"(smlops_[a-z_]+)"', source))
        for name, rule in RULES.items():
            for metric in re.findall(r"smlops_[a-z_]+", rule["expr"]):
                base = re.sub(r"_(total|bucket|count|sum)$", "", metric)
                self.assertIn(base, exported, f"{name} reads {metric}, which the server does not export")

    def test_every_alert_says_what_it_means(self):
        for name, rule in RULES.items():
            self.assertIn(rule["labels"]["severity"], {"warning", "critical"}, name)
            self.assertTrue(rule["annotations"]["summary"], name)
            self.assertIn("for", rule, f"{name} would fire on a single scrape")


if __name__ == "__main__":
    unittest.main()
