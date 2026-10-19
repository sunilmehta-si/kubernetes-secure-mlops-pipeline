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
        self.assertEqual(self.client.post("/v1/models/other:predict", json={"instances": []}).status_code, 404)
