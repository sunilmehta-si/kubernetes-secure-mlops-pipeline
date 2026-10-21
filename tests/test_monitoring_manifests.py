import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def docs(path: str) -> list[dict]:
    return [d for d in yaml.safe_load_all((ROOT / path).read_text()) if d]


DOCS = docs("deploy/monitoring/prometheus.yaml") + docs("deploy/monitoring/alerting.yaml")
DEPLOYMENTS = {d["metadata"]["name"]: d for d in DOCS if d["kind"] == "Deployment"}
POLICIES = [d for d in DOCS if d["kind"] == "NetworkPolicy"]


class MonitoringPodTests(unittest.TestCase):
    def test_every_pod_meets_pod_security_restricted(self):
        self.assertEqual(set(DEPLOYMENTS), {"prometheus", "alertmanager", "alert-relay"})
        for name, deployment in DEPLOYMENTS.items():
            spec = deployment["spec"]["template"]["spec"]
            with self.subTest(deployment=name):
                self.assertTrue(spec["securityContext"]["runAsNonRoot"])
                self.assertEqual(spec["securityContext"]["seccompProfile"]["type"], "RuntimeDefault")
                for c in spec["containers"]:
                    sc = c["securityContext"]
                    self.assertFalse(sc["allowPrivilegeEscalation"])
                    self.assertTrue(sc["readOnlyRootFilesystem"])
                    self.assertEqual(sc["capabilities"]["drop"], ["ALL"])
                    self.assertIn("limits", c["resources"])

    def test_images_are_pinned_by_digest_or_filled_in_by_digest(self):
        for name, deployment in DEPLOYMENTS.items():
            for c in deployment["spec"]["template"]["spec"]["containers"]:
                with self.subTest(deployment=name):
                    self.assertRegex(c["image"], r"(@sha256:[0-9a-f]{64}$|^\$\{SERVER_IMAGE\}$)")

    def test_only_prometheus_has_a_kubernetes_api_token(self):
        for name, deployment in DEPLOYMENTS.items():
            spec = deployment["spec"]["template"]["spec"]
            with self.subTest(deployment=name):
                if name == "prometheus":
                    self.assertEqual(spec["serviceAccountName"], "prometheus")         # pod discovery in serving
                else:
                    self.assertIs(spec["automountServiceAccountToken"], False)

    def test_prometheus_may_read_pods_in_serving_and_nothing_else(self):
        roles = [d for d in DOCS if d["kind"] in ("Role", "ClusterRole")]
        self.assertEqual([(r["kind"], r["metadata"]["namespace"]) for r in roles], [("Role", "serving")])
        self.assertEqual(roles[0]["rules"], [{"apiGroups": [""], "resources": ["pods"],
                                              "verbs": ["get", "list", "watch"]}])


class MonitoringNetworkTests(unittest.TestCase):
    def test_ingress_is_denied_except_along_the_alert_path(self):
        deny = [p for p in POLICIES if p["spec"]["podSelector"] == {}]
        self.assertEqual(len(deny), 1)
        self.assertEqual(deny[0]["spec"]["policyTypes"], ["Ingress"])
        self.assertNotIn("ingress", deny[0]["spec"])
        allowed = {}
        for p in POLICIES:
            if p["spec"]["podSelector"]:
                target = p["spec"]["podSelector"]["matchLabels"]["app"]
                (rule,) = p["spec"]["ingress"]
                allowed[target] = ({peer["podSelector"]["matchLabels"]["app"] for peer in rule["from"]},
                                   [port["port"] for port in rule["ports"]])
        self.assertEqual(allowed, {"alertmanager": ({"prometheus"}, [9093]),
                                   "alert-relay": ({"alertmanager", "prometheus"}, [9095])})

    def test_prometheus_listens_on_localhost_only(self):
        args = DEPLOYMENTS["prometheus"]["spec"]["template"]["spec"]["containers"][0]["args"]
        self.assertIn("--web.listen-address=127.0.0.1:9090", args)

    def test_alertmanager_reaches_the_relay_with_a_token_file_never_an_inline_secret(self):
        config = (ROOT / "deploy/monitoring/alertmanager.yml").read_text()
        receiver = next(r for r in yaml.safe_load(config)["receivers"] if r["name"] == "retrain-relay")
        (hook,) = receiver["webhook_configs"]
        self.assertEqual(hook["url"], "http://alert-relay.monitoring.svc:9095/alerts")
        self.assertEqual(set(hook["http_config"]["authorization"]), {"type", "credentials_file"})
        self.assertFalse(re.search(r"credentials:\s", config))


if __name__ == "__main__":
    unittest.main()
