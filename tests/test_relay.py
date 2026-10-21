import hashlib
import json
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from fastapi.testclient import TestClient

from secure_mlops.relay import EVENT_TYPE, DispatchError, create_relay, forwarded, github_dispatcher

TOKEN = "relay-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}
KEYS = {hashlib.sha256(TOKEN.encode()).hexdigest(): "alertmanager"}


def alert(name, status="firing", **labels):
    return {"status": status, "labels": {"alertname": name, **labels},
            "annotations": {"summary": f"{name} summary", "runbook": "internal notes"},
            "generatorURL": "http://prometheus/graph?g0.expr=secret", "fingerprint": "abc"}


DRIFT = {"alerts": [alert("TicketRouterTeamMixDrifted", action="retrain", severity="critical", gate="G7"),
                    alert("TicketRouterSlow", severity="warning")]}


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.sent, self.clock = [], Clock()
        app = create_relay(lambda event, payload: self.sent.append((event, payload)),
                           KEYS, cooldown_seconds=3600, clock=self.clock)
        self.client = TestClient(app)

    def test_a_firing_retrain_alert_dispatches_only_what_the_workflow_needs(self):
        r = self.client.post("/alerts", json=DRIFT, headers=AUTH)
        self.assertEqual(r.json()["dispatched"], True)
        ((event, payload),) = self.sent
        self.assertEqual(event, EVENT_TYPE)
        self.assertEqual(payload, {"alerts": [{"status": "firing", "labels": {
            "alertname": "TicketRouterTeamMixDrifted", "action": "retrain", "severity": "critical", "gate": "G7"},
            "annotations": {"summary": "TicketRouterTeamMixDrifted summary"}}]})

    def test_other_and_resolved_alerts_start_nothing(self):
        payload = {"alerts": [alert("TicketRouterSlow"), alert("TicketRouterNewVocabulary", "resolved",
                                                                action="retrain")]}
        self.assertEqual(self.client.post("/alerts", json=payload, headers=AUTH).json(),
                         {"retrain": False, "dispatched": False})
        self.assertEqual(self.sent, [])

    def test_repeats_within_the_cooldown_are_suppressed(self):
        self.client.post("/alerts", json=DRIFT, headers=AUTH)
        self.clock.now = 1800
        self.assertEqual(self.client.post("/alerts", json=DRIFT, headers=AUTH).json()["reason"], "cooldown")
        self.clock.now = 3601
        self.assertTrue(self.client.post("/alerts", json=DRIFT, headers=AUTH).json()["dispatched"])
        self.assertEqual(len(self.sent), 2)

    def test_a_missing_or_wrong_token_is_refused_before_anything_is_read(self):
        for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": TOKEN}):
            with self.subTest(headers=headers):
                r = self.client.post("/alerts", json=DRIFT, headers=headers)
                self.assertEqual(r.status_code, 401)
        self.assertEqual(self.sent, [])

    def test_garbage_is_a_400_not_a_crash(self):
        for body in (b"not json", b"[1, 2]", b'{"alerts": ["x"]}'):
            with self.subTest(body=body):
                self.assertEqual(self.client.post("/alerts", content=body, headers=AUTH).status_code, 400)

    def test_forwarded_caps_the_summary(self):
        long = {"alerts": [{"status": "firing", "labels": {"alertname": "A", "action": "retrain"},
                            "annotations": {"summary": "x" * 5000}}]}
        self.assertEqual(len(forwarded(long)[0]["annotations"]["summary"]), 300)

    def test_a_failed_dispatch_is_a_502_and_does_not_start_the_cooldown(self):
        calls = []

        def flaky(event, payload):
            calls.append(event)
            if len(calls) == 1:
                raise DispatchError("GitHub could not be reached")
        client = TestClient(create_relay(flaky, KEYS, clock=self.clock))
        self.assertEqual(client.post("/alerts", json=DRIFT, headers=AUTH).status_code, 502)
        self.assertTrue(client.post("/alerts", json=DRIFT, headers=AUTH).json()["dispatched"])


class FakeGitHub(BaseHTTPRequestHandler):
    status = 204
    received: list = []

    def do_POST(self):                                  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeGitHub.received.append((self.path, self.headers["Authorization"], body))
        self.send_response(FakeGitHub.status)
        self.end_headers()

    def log_message(self, *args):
        pass


class GitHubDispatcherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeGitHub)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.api = f"http://127.0.0.1:{cls.server.server_port}"
        cls.dir = tempfile.TemporaryDirectory()
        cls.token = Path(cls.dir.name, "token")
        cls.token.write_text("ghp_test\n")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.dir.cleanup()

    def setUp(self):
        FakeGitHub.received, FakeGitHub.status = [], 204

    def test_posts_the_dispatch_with_the_token_from_the_file(self):
        github_dispatcher("sunilmehta-si/kubernetes-secure-mlops-pipeline", self.token, api=self.api)(
            EVENT_TYPE, {"alerts": []})
        ((path, auth, body),) = FakeGitHub.received
        self.assertEqual(path, "/repos/sunilmehta-si/kubernetes-secure-mlops-pipeline/dispatches")
        self.assertEqual(auth, "Bearer ghp_test")
        self.assertEqual(body, {"event_type": "drift-alert", "client_payload": {"alerts": []}})

    def test_refusals_and_bad_repositories_are_errors(self):
        FakeGitHub.status = 404
        with self.assertRaisesRegex(DispatchError, "HTTP 404"):
            github_dispatcher("a/b", self.token, api=self.api)(EVENT_TYPE, {})
        with self.assertRaisesRegex(DispatchError, "could not be reached"):
            github_dispatcher("a/b", self.token, api="http://127.0.0.1:9", timeout=2)(EVENT_TYPE, {})
        with self.assertRaisesRegex(ValueError, "owner/name"):
            github_dispatcher("a/b/../c", self.token)


if __name__ == "__main__":
    unittest.main()
