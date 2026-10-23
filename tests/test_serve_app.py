import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from secure_mlops.serve.app import create_app
from secure_mlops.serve.auth import digest
from secure_mlops.serve.bundle import load_verified
from secure_mlops.serve.feedback import PredictionLog
from secure_mlops.serve.live_drift import LiveDrift
from tests.test_serve_bundle import make_bundle

KEY = "smk_test-key"
KEYS = {digest(KEY): "tester"}
AUTH = {"Authorization": f"Bearer {KEY}"}


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        _, public = make_bundle(Path(cls.dir.name, "b"), Path(cls.dir.name, "keys"))
        cls.served = load_verified(Path(cls.dir.name, "b"), public)
        cls.client = TestClient(create_app(cls.served, KEYS), headers=AUTH)

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def test_health_and_readiness_name_the_served_model(self):
        self.assertEqual(self.client.get("/healthz").json(), {"status": "ok"})
        ready = self.client.get("/v1/models/ticket-router").json()
        self.assertEqual((ready["ready"], ready["version"], ready["digest"]), (True, "4", self.served.digest))
        self.assertEqual(self.client.get("/v1/models/other").status_code, 404)

    def test_predict_routes_tickets_and_says_which_model_answered(self):
        r = self.client.post("/v1/models/ticket-router:predict", json={"instances": [
            {"text": "please refund the $40 charge from last week"},
            {"text": "the app crashes with error E404 when I open settings"}]})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual([p["team"] for p in body["predictions"]], ["billing", "technical"])
        self.assertTrue(all(0 < p["confidence"] <= 1 for p in body["predictions"]))
        self.assertEqual(body["model"]["digest"], self.served.digest)
        valid = {"instances": [{"text": "my parcel never arrived"}]}
        self.assertEqual(self.client.post("/v1/models/other:predict", json=valid).status_code, 404)


class InputLimitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        _, public = make_bundle(Path(cls.dir.name, "b"), Path(cls.dir.name, "keys"))
        cls.client = TestClient(create_app(load_verified(Path(cls.dir.name, "b"), public), KEYS), headers=AUTH)

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def post(self, body):
        return self.client.post("/v1/models/ticket-router:predict", json=body)

    def test_schema_limits(self):
        from secure_mlops.serve.app import MAX_INSTANCES, MAX_TEXT
        ok = {"text": "my parcel never arrived"}
        for body in ({"instances": []}, {"instances": [ok] * (MAX_INSTANCES + 1)}, {"instances": [{"text": ""}]},
                     {"instances": [{"text": "x" * (MAX_TEXT + 1)}]}, {"instances": [{**ok, "label": "billing"}]},
                     {"instances": [ok], "debug": True}, {"inputs": [ok]}):
            with self.subTest(body=str(body)[:60]):
                self.assertEqual(self.post(body).status_code, 422)
        self.assertEqual(self.post({"instances": [ok] * MAX_INSTANCES}).status_code, 200)

    def test_oversized_bodies_are_refused_before_parsing(self):
        from secure_mlops.serve.app import MAX_BODY_BYTES
        big = b'{"instances": [' + b'{"text": "x"},' * (MAX_BODY_BYTES // 14) + b'{"text": "x"}]}'
        r = self.client.post("/v1/models/ticket-router:predict", content=big, headers={"content-type": "application/json"})
        self.assertEqual(r.status_code, 413)
        r = self.client.post("/v1/models/ticket-router:predict", content=b"{}",
                             headers={"content-type": "application/json", "content-length": "abc"})
        self.assertEqual(r.status_code, 413)


class AuditLogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        _, public = make_bundle(Path(cls.dir.name, "b"), Path(cls.dir.name, "keys"))
        cls.served = load_verified(Path(cls.dir.name, "b"), public)
        cls.client = TestClient(create_app(cls.served, KEYS), headers=AUTH)

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def test_one_line_per_request_with_identity_and_never_the_text(self):
        import json
        secret = "my card ending 4242 was charged twice"
        with self.assertLogs("smlops.audit", level="INFO") as logs:
            r = self.client.post("/v1/models/ticket-router:predict", headers={"X-Request-ID": "req-123"},
                                 json={"instances": [{"text": secret}, {"text": "parcel never arrived"}]})
        self.assertEqual((r.headers["x-request-id"], r.headers["x-model-digest"]), ("req-123", self.served.digest))
        self.assertEqual(len(logs.records), 1)
        line = json.loads(logs.records[0].getMessage())
        self.assertEqual((line["request_id"], line["version"], line["tickets"]), ("req-123", "4", 2))
        self.assertEqual(sum(line["teams"].values()), 2)
        self.assertNotIn("4242", logs.output[0])
        self.assertNotIn("charged", logs.output[0])

    def test_unsafe_request_ids_are_replaced(self):
        with self.assertLogs("smlops.audit", level="INFO"):
            r = self.client.post("/v1/models/ticket-router:predict", headers={"X-Request-ID": "bad id\nINJECT"},
                                 json={"instances": [{"text": "parcel never arrived"}]})
        self.assertRegex(r.headers["x-request-id"], r"^[0-9a-f]{32}$")


class MetricsTests(unittest.TestCase):
    def test_requests_latency_teams_and_model_identity_are_exported(self):
        with tempfile.TemporaryDirectory() as d:
            _, public = make_bundle(Path(d, "b"), Path(d, "keys"))
            served = load_verified(Path(d, "b"), public)
            client = TestClient(create_app(served, KEYS), headers=AUTH)
            client.post("/v1/models/ticket-router:predict", json={"instances": [{"text": "parcel never arrived"}]})
            client.post("/v1/models/ticket-router:predict", json={"instances": []})
            text = client.get("/metrics").text
        self.assertIn('smlops_predict_requests_total{status="200"} 1.0', text)
        self.assertIn('smlops_predict_requests_total{status="422"} 1.0', text)
        self.assertIn("smlops_predict_latency_seconds_count 2.0", text)
        self.assertRegex(text, r'smlops_tickets_routed_total\{team="\w+"\} 1\.0')
        self.assertIn(f'smlops_model_info{{digest="{served.digest}",name="ticket-router",version="4"}} 1.0', text)
        self.assertIn("smlops_live_window_tickets 1.0", text)
        self.assertNotIn("smlops_live_psi_predicted_team", text)       # one ticket is not a distribution

    def test_live_drift_is_exported_once_the_window_is_full(self):
        with tempfile.TemporaryDirectory() as d:
            _, public = make_bundle(Path(d, "b"), Path(d, "keys"))
            served = load_verified(Path(d, "b"), public)
            drift = LiveDrift({"billing": 0.5, "shipping": 0.5}, frozenset({"parcel", "never", "arrived"}), window=4,
                              min_tickets=2, baseline_new_word_rate=0.25)
            client = TestClient(create_app(served, KEYS, drift=drift), headers=AUTH)
            client.post("/v1/models/ticket-router:predict",
                        json={"instances": [{"text": "parcel never arrived"}, {"text": "quantum flux capacitor"}]})
            text = client.get("/metrics").text
        self.assertIn("smlops_live_window_tickets 2.0", text)
        self.assertIn("smlops_live_new_word_rate 0.5", text)
        self.assertIn("smlops_live_new_word_ratio 2.0", text)
        self.assertRegex(text, r"smlops_live_psi_predicted_team \d")

    def test_each_prediction_is_logged_for_its_client_under_its_request_id(self):
        log = PredictionLog()
        with tempfile.TemporaryDirectory() as d:
            _, public = make_bundle(Path(d, "b"), Path(d, "keys"))
            client = TestClient(create_app(load_verified(Path(d, "b"), public), KEYS, predictions=log), headers=AUTH)
            r = client.post("/v1/models/ticket-router:predict", headers={"X-Request-ID": "req-7"},
                            json={"instances": [{"text": "parcel never arrived"}, {"text": "refund my charge"}]})
        teams = [p["team"] for p in r.json()["predictions"]]
        self.assertEqual(log.confirm("req-7", "tester", ["shipping", "billing"]),
                         list(zip(teams, ["shipping", "billing"])))


class AuthEnforcementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        _, public = make_bundle(Path(cls.dir.name, "b"), Path(cls.dir.name, "keys"))
        cls.app = create_app(load_verified(Path(cls.dir.name, "b"), public), KEYS)

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def test_predictions_need_a_valid_key_and_the_audit_names_the_client(self):
        import json
        body = {"instances": [{"text": "parcel never arrived"}]}
        anonymous = TestClient(self.app)
        for headers in ({}, {"Authorization": "Bearer smk_wrong"}, {"Authorization": KEY}, {"X-API-Key": KEY}):
            with self.subTest(headers=headers):
                r = anonymous.post("/v1/models/ticket-router:predict", json=body, headers=headers)
                self.assertEqual((r.status_code, r.headers.get("www-authenticate")), (401, "Bearer"))
        with self.assertLogs("smlops.audit", level="INFO") as logs:
            r = anonymous.post("/v1/models/ticket-router:predict", json=body, headers=AUTH)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(json.loads(logs.records[0].getMessage())["client"], "tester")
        self.assertNotIn(KEY, logs.output[0])

    def test_health_and_metrics_stay_open_for_the_platform(self):
        anonymous = TestClient(self.app)
        self.assertEqual(anonymous.get("/healthz").status_code, 200)
        self.assertEqual(anonymous.get("/metrics").status_code, 200)
