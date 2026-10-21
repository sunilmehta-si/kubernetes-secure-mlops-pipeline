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
        source = "".join((ROOT / p).read_text() for p in ("secure_mlops/serve/app.py", "secure_mlops/relay.py"))
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


class RelayDeploymentTests(unittest.TestCase):
    def relay(self, path):
        docs = [d for d in yaml.safe_load_all((ROOT / path).read_text()) if d]
        return next(d for d in docs if d["kind"] == "Deployment" and d["metadata"]["name"] == "alert-relay")

    def test_ci_runs_a_dry_run_and_production_patch_dispatches_to_this_repo(self):
        from secure_mlops.cli import _parser
        ci = self.relay("deploy/monitoring/alerting.yaml")["spec"]["template"]["spec"]
        patch = self.relay("deploy/monitoring/relay-github-patch.yaml")["spec"]["template"]["spec"]
        (ci_container,), (prod_container,) = ci["containers"], patch["containers"]
        self.assertEqual(prod_container["name"], ci_container["name"])            # the patch hits the container
        ci_args, prod_args = (_parser().parse_args(c["args"]) for c in (ci_container, prod_container))
        self.assertTrue(ci_args.dry_run)
        self.assertFalse(prod_args.dry_run)
        self.assertEqual(prod_args.repo, "sunilmehta-si/kubernetes-secure-mlops-pipeline")
        self.assertEqual(prod_args.api_keys, ci_args.api_keys)
        mount = prod_container["volumeMounts"][0]
        self.assertTrue(prod_args.github_token_file.startswith(mount["mountPath"] + "/"))
        self.assertTrue(mount["readOnly"])
        self.assertEqual(patch["volumes"][0]["secret"]["secretName"], "alert-relay-github")


class MonitoringDocTests(unittest.TestCase):
    DOC = (ROOT / "docs/monitoring.md").read_text()

    def test_every_alert_is_documented(self):
        table = [line for line in self.DOC.splitlines() if line.startswith("| TicketRouter")]
        documented = {name.strip() for line in table for name in line.split("|")[1].split(",")}
        self.assertEqual(documented, set(RULES))

    def test_every_relative_link_resolves(self):
        for target in re.findall(r"\]\((\.\./[^)#]+)\)", self.DOC):
            self.assertTrue((ROOT / "docs" / target).resolve().exists(), target)


if __name__ == "__main__":
    unittest.main()
