import json
import tempfile
import unittest
from pathlib import Path

from secure_mlops.data import DataSpec
from secure_mlops.dataset import read
from secure_mlops.retrain import combine, retrain_reasons
from secure_mlops.steps import StepError, prepare


class CombineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        cls.base, cls.recent = Path(cls.dir.name, "base"), Path(cls.dir.name, "recent")
        prepare(DataSpec(n=1000, seed=7), cls.base)
        prepare(DataSpec(n=1000, seed=11, drift=0.6), cls.recent)

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def test_trains_on_both_and_judges_on_recent_traffic_only(self):
        out = Path(self.dir.name, "combined")
        manifest = combine(self.base, self.recent, out)
        train, test = read(out, "train"), read(out, "test")
        self.assertTrue(train["ticket_id"].str.startswith("T007-").any())
        self.assertTrue(train["ticket_id"].str.startswith("T011-").any())
        self.assertTrue(test["ticket_id"].str.startswith("T011-").all())
        self.assertFalse(train["text"].isin(set(test["text"])).any())          # no test text leaks into training
        self.assertEqual(manifest["rows"]["test"], len(read(self.recent, "test")))
        self.assertEqual(json.loads((out / "manifest.json").read_text()), manifest)

    def test_the_same_tickets_twice_are_refused(self):
        with self.assertRaisesRegex(StepError, "share ticket ids"):
            combine(self.base, self.base, Path(self.dir.name, "twice"))


class AlertTests(unittest.TestCase):
    def alert(self, name, status="firing", **labels):
        return {"status": status, "labels": {"alertname": name, **labels},
                "annotations": {"summary": f"{name} summary"}}

    def test_only_firing_retrain_alerts_count(self):
        payload = {"alerts": [self.alert("TicketRouterTeamMixDrifted", action="retrain"),
                              self.alert("TicketRouterNewVocabulary", status="resolved", action="retrain"),
                              self.alert("TicketRouterSlow"),
                              self.alert("TicketRouterTeamMixDrifted", action="retrain")]}
        self.assertEqual(retrain_reasons(payload), ["TicketRouterTeamMixDrifted: TicketRouterTeamMixDrifted summary"])

    def test_an_empty_or_unrelated_payload_starts_nothing(self):
        self.assertEqual(retrain_reasons({}), [])
        self.assertEqual(retrain_reasons({"alerts": [self.alert("TicketRouterDown")]}), [])


if __name__ == "__main__":
    unittest.main()


class AlertRulesStartRetrainingTests(unittest.TestCase):
    def test_exactly_the_retrain_alerts_in_alerts_yaml_start_retraining(self):
        import yaml

        from secure_mlops.relay import forwarded
        root = Path(__file__).resolve().parents[1]
        rules = [r for g in yaml.safe_load((root / "deploy/monitoring/alerts.yaml").read_text())["groups"]
                 for r in g["rules"]]
        self.assertGreaterEqual(sum(r["labels"].get("action") == "retrain" for r in rules), 3)
        for rule in rules:
            payload = {"alerts": [{"status": "firing", "labels": {"alertname": rule["alert"], **rule["labels"]},
                                   "annotations": {"summary": "s"}}]}
            starts = rule["labels"].get("action") == "retrain"
            with self.subTest(alert=rule["alert"]):
                self.assertEqual(bool(retrain_reasons(payload)), starts)       # the workflow's own check
                self.assertEqual(bool(forwarded(payload)), starts)             # what the relay sends on
