import copy
import tempfile
import unittest
from pathlib import Path

from secure_mlops.gate import GatePolicy, decide, load_policy

ROOT = Path(__file__).resolve().parents[1]
GOOD = {"n": 450, "accuracy": 0.95, "macro_f1": 0.95, "calibration_error": 0.04, "latency_ms": {"p50": 1.0, "p95": 2.0},
        "recall_by_label": {"billing": 0.95, "technical": 0.96, "account": 0.94, "shipping": 0.95, "security": 0.93},
        "slices": {"channel=chat": {"n": 140, "accuracy": 0.94, "macro_f1": 0.94},
                   "tier=enterprise": {"n": 20, "accuracy": 0.9, "macro_f1": 0.9}}}


class GateTests(unittest.TestCase):
    def test_first_model_is_judged_on_floors(self):
        d = decide(GOOD, None, GatePolicy())
        self.assertTrue(d["passed"], d["failed"])
        self.assertFalse(d["compared_with_champion"])
        self.assertTrue(all(c["kind"] == "floor" for c in d["checks"]))

    def test_floors_fail_on_one_weak_team(self):
        weak = copy.deepcopy(GOOD)
        weak["recall_by_label"]["security"] = 0.7
        self.assertEqual(decide(weak, None, GatePolicy())["failed"], ["recall[security]"])

    def test_regression_on_a_large_slice_blocks_but_a_small_slice_does_not(self):
        challenger = copy.deepcopy(GOOD)
        challenger["slices"]["channel=chat"]["macro_f1"] = 0.88        # -0.06 on 140 tickets
        challenger["slices"]["tier=enterprise"]["macro_f1"] = 0.70     # -0.20 on 20 tickets: too few to judge
        d = decide(challenger, GOOD, GatePolicy())
        self.assertEqual(d["failed"], ["drop in macro_f1[channel=chat]"])
        self.assertNotIn("drop in macro_f1[tier=enterprise]", [c["name"] for c in d["checks"]])

    def test_equal_or_better_passes_and_test_sets_must_match(self):
        self.assertTrue(decide(GOOD, GOOD, GatePolicy())["passed"])
        with self.assertRaisesRegex(ValueError, "same test set"):
            decide(GOOD, {**GOOD, "n": 10}, GatePolicy())

    def test_committed_policy_and_unknown_keys(self):
        self.assertEqual(load_policy(ROOT / "configs/gate.yaml"), GatePolicy())
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "g.yaml"
            p.write_text("min_acuracy: 0.9\n")
            with self.assertRaisesRegex(ValueError, "unknown gate key"):
                load_policy(p)
