import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from secure_mlops.serve.app import create_app
from secure_mlops.serve.bundle import load_verified
from tests.test_serve_bundle import make_bundle


class AppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.TemporaryDirectory()
        _, public = make_bundle(Path(cls.dir.name, "b"), Path(cls.dir.name, "keys"))
        cls.served = load_verified(Path(cls.dir.name, "b"), public)
        cls.client = TestClient(create_app(cls.served))

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
        cls.client = TestClient(create_app(load_verified(Path(cls.dir.name, "b"), public)))

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
        cls.client = TestClient(create_app(cls.served))

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
