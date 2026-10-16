import unittest

import numpy as np
import pandas as pd

from secure_mlops.config import TrainConfig
from secure_mlops.data import LABELS, DataSpec, generate
from secure_mlops.dataset import split
from secure_mlops.train import features, train

SPLITS = split(pd.DataFrame(generate(DataSpec(n=1500))))


class TrainTests(unittest.TestCase):
    def test_learns_the_task_but_not_perfectly(self):
        model = train(SPLITS.train, TrainConfig())
        accuracy = (model.predict(features(SPLITS.test)) == SPLITS.test["label"]).mean()
        self.assertGreater(accuracy, 0.8)               # the classes are learnable
        self.assertLess(accuracy, 0.995)                # but overlap and label noise keep it honest
        self.assertEqual(sorted(model.classes_), sorted(LABELS))

    def test_deterministic(self):
        a, b = train(SPLITS.train, TrainConfig()), train(SPLITS.train, TrainConfig())
        x = features(SPLITS.test)
        np.testing.assert_array_equal(a.predict_proba(x), b.predict_proba(x))

    def test_routes_the_text_column_not_its_name(self):
        model = train(SPLITS.train, TrainConfig())
        tickets = pd.DataFrame({"text": ["please refund the $40 charge from last week",
                                         "the app crashes with error E404 when I open settings"]})
        self.assertEqual(list(model.predict(tickets)), ["billing", "technical"])

    def test_refuses_data_missing_a_team(self):
        with self.assertRaisesRegex(ValueError, "no tickets for: security"):
            train(SPLITS.train[SPLITS.train["label"] != "security"], TrainConfig())
