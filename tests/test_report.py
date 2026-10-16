import unittest

from secure_mlops.gate import GatePolicy, decide
from secure_mlops.report import gate_markdown
from tests.test_gate import GOOD


class ReportTests(unittest.TestCase):
    def test_failures_come_first_with_their_limits(self):
        weak = {**GOOD, "accuracy": 0.80}
        md = gate_markdown(decide(weak, GOOD, GatePolicy()), "ticket-router v2")
        self.assertIn("## Evaluation gate: ticket-router v2 **failed**", md)
        self.assertIn("Failed: accuracy, drop in accuracy.", md)
        rows = [line for line in md.splitlines() if line.startswith("| ") and "Check" not in line]
        self.assertEqual(rows[0], "| accuracy | 0.8000 | >= 0.9000 | **FAIL** |")
        self.assertIn("| drop in accuracy | +0.1500 | <= 0.0100 | **FAIL** |", md)
        self.assertIn("| latency_p95_ms | 2.00 ms | <= 25.00 ms | pass |", md)

    def test_first_model_wording(self):
        md = gate_markdown(decide(GOOD, None, GatePolicy()))
        self.assertIn("**passed**", md)
        self.assertIn("floors only (no champion yet)", md)
