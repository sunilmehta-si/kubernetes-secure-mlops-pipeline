import unittest

import pandas as pd

from secure_mlops.config import TrainConfig
from secure_mlops.data import DataSpec, generate
from secure_mlops.dataset import split
from secure_mlops.profile import reference_profile
from secure_mlops.serve.live_drift import LiveDrift, model_vocabulary
from secure_mlops.train import features, train

SPLITS = split(pd.DataFrame(generate(DataSpec(n=3000))))
MODEL = train(SPLITS.train, TrainConfig())
PROFILE = reference_profile(SPLITS.train, MODEL, SPLITS.test)
REFERENCE = PROFILE["teams"]


def watch(drift: float) -> dict:
    monitor = LiveDrift(REFERENCE, model_vocabulary(MODEL), window=500, min_tickets=200,
                        baseline_new_word_rate=PROFILE["new_word_rate"])
    batch = pd.DataFrame(generate(DataSpec(n=500, seed=303, drift=drift)))
    monitor.observe(batch["text"], MODEL.predict(features(batch)))
    return monitor.snapshot()


class LiveDriftTests(unittest.TestCase):
    def test_vocabulary_is_the_models_single_words(self):
        vocab = model_vocabulary(MODEL)
        self.assertIn("refund", vocab)
        self.assertFalse(any(" " in w for w in vocab))

    def test_clean_traffic_is_quiet_and_shifted_traffic_stands_out(self):
        clean, shifted = watch(0.0), watch(1.0)
        self.assertLess(clean["psi_predicted_team"], 0.10)
        self.assertLess(clean["new_word_ratio"], 1.15)                    # about the model's own baseline
        self.assertGreater(shifted["psi_predicted_team"], clean["psi_predicted_team"])
        self.assertGreater(shifted["new_word_ratio"], 1.3)                # the live alert threshold

    def test_nothing_is_reported_before_the_window_is_meaningful(self):
        monitor = LiveDrift(REFERENCE, model_vocabulary(MODEL), window=500, min_tickets=200)
        monitor.observe(["my parcel never arrived"] * 50, ["shipping"] * 50)
        self.assertEqual(monitor.snapshot(), {"tickets": 50, "psi_predicted_team": None, "new_word_rate": None,
                                              "new_word_ratio": None})
        with self.assertRaises(ValueError):
            LiveDrift(REFERENCE, frozenset(), window=10, min_tickets=20)
