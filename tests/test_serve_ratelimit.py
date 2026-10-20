import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from secure_mlops.serve.app import create_app
from secure_mlops.serve.bundle import load_verified
from secure_mlops.serve.ratelimit import TicketLimiter
from tests.test_serve_app import AUTH, KEYS
from tests.test_serve_bundle import make_bundle


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class LimiterTests(unittest.TestCase):
    def test_burst_then_refill_measured_in_tickets_per_client(self):
        clock = Clock()
        limiter = TicketLimiter(per_minute=60, burst=10, clock=clock)
        self.assertEqual(limiter.take("a", 10), (True, 0))
        self.assertEqual(limiter.take("a", 1), (False, 1))           # empty: one ticket refills in a second
        self.assertEqual(limiter.take("b", 5), (True, 0))            # other clients are unaffected
        clock.t = 5.0
        self.assertEqual(limiter.take("a", 5), (True, 0))
        self.assertEqual(limiter.take("a", 3)[0], False)
        self.assertEqual(limiter.take("a", 11), (False, -1))         # larger than any burst: never allowed
        with self.assertRaises(ValueError):
            TicketLimiter(per_minute=0)

    def test_endpoint_answers_429_with_retry_after_and_counts_it(self):
        with tempfile.TemporaryDirectory() as d:
            _, public = make_bundle(Path(d, "b"), Path(d, "keys"))
            clock = Clock()
            app = create_app(load_verified(Path(d, "b"), public), KEYS, TicketLimiter(per_minute=60, burst=3, clock=clock))
            client = TestClient(app, headers=AUTH)
            body = lambda n: {"instances": [{"text": "parcel never arrived"}] * n}   # noqa: E731
            self.assertEqual(client.post("/v1/models/ticket-router:predict", json=body(3)).status_code, 200)
            r = client.post("/v1/models/ticket-router:predict", json=body(2))
            self.assertEqual((r.status_code, r.headers["retry-after"]), (429, "2"))
            self.assertEqual(client.post("/v1/models/ticket-router:predict", json=body(4)).status_code, 413)
            self.assertIn('smlops_predict_requests_total{status="429"} 1.0', client.get("/metrics").text)
