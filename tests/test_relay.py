import hashlib
import unittest

from fastapi.testclient import TestClient

from secure_mlops.relay import EVENT_TYPE, create_relay, forwarded

TOKEN = "relay-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


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
                           hashlib.sha256(TOKEN.encode()).hexdigest(), cooldown_seconds=3600, clock=self.clock)
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


if __name__ == "__main__":
    unittest.main()
