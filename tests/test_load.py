import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from secure_mlops.load import LoadError, ServingObjectives, judge, load_objectives, markdown, run

ROOT = Path(__file__).resolve().parents[1]


class FakeEndpoint(BaseHTTPRequestHandler):
    slow_every = 0          # every nth request sleeps 50 ms
    fail_every = 0          # every nth request answers 500
    throttle_every = 0      # every nth request answers 429, as the server's ticket budget does
    seen = 0
    lock = threading.Lock()

    def do_POST(self):                                   # noqa: N802
        n = int(self.headers["Content-Length"])
        body = json.loads(self.rfile.read(n))
        with FakeEndpoint.lock:
            FakeEndpoint.seen += 1
            i = FakeEndpoint.seen
        if self.headers.get("Authorization") != "Bearer smk_test":
            return self._answer(401, {"detail": "a valid API key is required"})
        if FakeEndpoint.slow_every and i % FakeEndpoint.slow_every == 0:
            time.sleep(0.05)
        if FakeEndpoint.fail_every and i % FakeEndpoint.fail_every == 0:
            return self._answer(500, {"detail": "boom"})
        if FakeEndpoint.throttle_every and i % FakeEndpoint.throttle_every == 0:
            return self._answer(429, {"detail": "rate limit reached"})
        self._answer(200, {"predictions": [{"team": "billing", "confidence": 0.9} for _ in body["instances"]]})

    def _answer(self, status, payload):
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class LoadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeEndpoint)
        cls.server.daemon_threads = True
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f"http://127.0.0.1:{cls.server.server_port}/v1/models/ticket-router:predict"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def setUp(self):
        FakeEndpoint.slow_every = FakeEndpoint.fail_every = FakeEndpoint.throttle_every = FakeEndpoint.seen = 0

    def test_measures_every_request_and_passes_a_healthy_endpoint(self):
        report = run(self.url, "smk_test", requests_total=60, concurrency=6)
        self.assertEqual((report["requests"], report["errors"], report["status_counts"]), (60, 0, {"200": 60}))
        self.assertLessEqual(report["latency_ms"]["p50"], report["latency_ms"]["p95"])
        self.assertLessEqual(report["latency_ms"]["p95"], report["latency_ms"]["max"])
        verdict = judge(report, ServingObjectives(p95_ms=5000, max_error_rate=0.0, min_requests=50))
        self.assertTrue(verdict["passed"])
        self.assertIn("**passed**", markdown(report, verdict))

    def test_errors_and_slow_tails_fail_the_objectives(self):
        FakeEndpoint.fail_every = 10                      # 10% errors
        FakeEndpoint.slow_every = 7                       # a slow tail above a tight p95
        report = run(self.url, "smk_test", requests_total=50, concurrency=5)
        self.assertEqual(report["status_counts"]["500"], 5)
        verdict = judge(report, ServingObjectives(p95_ms=20, max_error_rate=0.01, min_requests=50))
        self.assertFalse(verdict["passed"])
        self.assertEqual([c["passed"] for c in verdict["checks"]], [False, False])
        self.assertIn("**FAIL**", markdown(report, verdict))

    def test_a_wrong_key_is_an_error_and_a_dead_endpoint_counts_as_no_answer(self):
        report = run(self.url, "wrong", requests_total=5, concurrency=1)
        self.assertEqual(report["status_counts"], {"401": 5})
        self.assertEqual(report["errors"], 0)             # a 401 is the client's fault, not the server's
        dead = run("http://127.0.0.1:9/v1/models/x:predict", "smk_test", requests_total=3, concurrency=1,
                   timeout=1)
        self.assertEqual((dead["errors"], dead["status_counts"]), (3, {"0": 3}))

    def test_too_few_requests_are_not_judged_and_the_committed_objectives_load(self):
        objectives = load_objectives(ROOT / "configs/serving.yaml")
        self.assertEqual(objectives, ServingObjectives())
        with self.assertRaisesRegex(LoadError, "too few to judge"):
            judge({"requests": 10, "latency_ms": {"p95": 1}, "error_rate": 0}, objectives)
        with tempfile.TemporaryDirectory() as d:
            Path(d, "s.yaml").write_text("p95_ms: 100\nlatency_budget: 1\n")
            with self.assertRaisesRegex(LoadError, "unknown serving objective"):
                load_objectives(Path(d, "s.yaml"))
        with self.assertRaises(LoadError):
            run(self.url, "k", requests_total=0, concurrency=1)

    def test_a_throttled_run_is_refused_not_passed(self):
        FakeEndpoint.throttle_every = 2
        report = run(self.url, "smk_test", requests_total=20, concurrency=4)
        self.assertEqual((report["status_counts"]["429"], report["errors"]), (10, 0))   # not errors, not a pass
        with self.assertRaisesRegex(LoadError, "10 of 20 requests were throttled"):
            judge(report, ServingObjectives(min_requests=10))

    def test_pacing_spreads_the_run_over_time(self):
        started = time.perf_counter()
        report = run(self.url, "smk_test", requests_total=10, concurrency=4, rate=50)
        self.assertGreaterEqual(time.perf_counter() - started, 0.18)          # 10 requests at 50/s
        self.assertEqual(report["rate"], 50)
        self.assertIn("paced at 50 a second", markdown(report, judge(report, ServingObjectives(min_requests=10))))
        with self.assertRaisesRegex(LoadError, "rate must be positive"):
            run(self.url, "smk_test", requests_total=5, concurrency=1, rate=0)

    def test_bounds_are_checked_before_any_request_is_sent(self):
        for kwargs in ({"concurrency": 0}, {"tickets_per_request": 0}, {"tickets_per_request": 65}):
            with self.subTest(**kwargs), self.assertRaisesRegex(LoadError, "between 1 and 64"):
                run("http://127.0.0.1:9/never-reached", "k", requests_total=5, **{"concurrency": 1, **kwargs})


if __name__ == "__main__":
    unittest.main()
