import tempfile
import unittest
from collections import Counter
from pathlib import Path

import pandas as pd

from secure_mlops.config import TrainConfig
from secure_mlops.data import DataSpec, generate
from secure_mlops.dataset import split
from secure_mlops.drift import DriftPolicy, check, load_policy, psi
from secure_mlops.train import features, train

ROOT = Path(__file__).resolve().parents[1]
SPLITS = split(pd.DataFrame(generate(DataSpec(n=3000))))
MODEL = train(SPLITS.train, TrainConfig())
REF_ACC = float((MODEL.predict(features(SPLITS.validation)) == SPLITS.validation["label"]).mean())


def batch(drift, n=600):
    return pd.DataFrame(generate(DataSpec(n=n, seed=101, drift=drift)))


class DriftTests(unittest.TestCase):
    def test_psi_is_zero_for_the_same_mix_and_grows_with_shift(self):
        self.assertAlmostEqual(psi(Counter(a=50, b=50), Counter(a=5, b=5)), 0.0)
        self.assertGreater(psi(Counter(a=50, b=50), Counter(a=90, b=10)), 0.25)

    def test_clean_batch_is_ok_and_a_shifted_batch_alerts(self):
        policy = load_policy(ROOT / "configs/drift.yaml")
        clean = check(MODEL, SPLITS.train, batch(0.0), policy, REF_ACC)
        self.assertEqual((clean["level"], clean["retrain"]), ("ok", False), clean["checks"])
        shifted = check(MODEL, SPLITS.train, batch(1.0), policy, REF_ACC)
        self.assertEqual((shifted["level"], shifted["retrain"]), ("alert", True))
        levels = {c["name"]: c["level"] for c in shifted["checks"]}
        self.assertEqual((levels["psi[channel]"], levels["new word rate"]), ("alert", "alert"))
        self.assertEqual(levels["accuracy drop"], "ok")       # drift is a reason to look, not proof of harm

    def test_small_batches_and_unknown_keys_are_refused(self):
        with self.assertRaisesRegex(ValueError, "at least 200"):
            check(MODEL, SPLITS.train, batch(0.0, n=100), DriftPolicy())
        with tempfile.TemporaryDirectory() as d:
            Path(d, "d.yaml").write_text("psi_warm: 0.1\n")
            with self.assertRaisesRegex(ValueError, "unknown drift key"):
                load_policy(Path(d, "d.yaml"))
