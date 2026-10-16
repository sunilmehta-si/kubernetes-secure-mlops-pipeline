import unittest

import numpy as np
import pandas as pd

from secure_mlops.config import TrainConfig
from secure_mlops.data import LABELS, DataSpec, generate
from secure_mlops.dataset import split
from secure_mlops.evaluate import calibration_error, evaluate
from secure_mlops.train import train

SPLITS = split(pd.DataFrame(generate(DataSpec(n=1500))))
MODEL = train(SPLITS.train, TrainConfig())


class EvaluateTests(unittest.TestCase):
    def test_report_has_overall_per_label_slices_and_latency(self):
        r = evaluate(MODEL, SPLITS.test)
        self.assertEqual(r["n"], len(SPLITS.test))
        self.assertEqual(set(r["recall_by_label"]), set(LABELS))
        self.assertIn("channel=chat", r["slices"])
        self.assertIn("length=short", r["slices"])
        self.assertEqual(sum(v["n"] for k, v in r["slices"].items() if k.startswith("tier=")), r["n"])
        self.assertLess(r["latency_ms"]["p50"], r["latency_ms"]["p95"] + 1e-9)
        self.assertGreater(r["latency_ms"]["p95"], 0)
        self.assertNotIn("latency_ms", evaluate(MODEL, SPLITS.test, measure_latency=False))

    def test_calibration_error(self):
        classes = ["a", "b"]
        perfect = np.array([[1.0, 0.0], [0.0, 1.0]])
        self.assertEqual(calibration_error(perfect, np.array(["a", "b"]), classes), 0.0)
        overconfident = np.array([[0.99, 0.01]] * 4)
        self.assertGreater(calibration_error(overconfident, np.array(["a", "b", "b", "b"]), classes), 0.7)
