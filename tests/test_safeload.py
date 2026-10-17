import os
import tempfile
import unittest
from pathlib import Path

import mlflow.sklearn
import pandas as pd

from secure_mlops.config import TrainConfig
from secure_mlops.data import DataSpec, generate
from secure_mlops.dataset import split
from secure_mlops.safeload import UnsafeModelError, load, model_format
from secure_mlops.steps import save_model
from secure_mlops.train import train

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
MODEL = train(split(pd.DataFrame(generate(DataSpec(n=1000)))).train, TrainConfig())


class SafeLoadTests(unittest.TestCase):
    def test_skops_models_load(self):
        with tempfile.TemporaryDirectory() as d:
            save_model(MODEL, Path(d) / "m")
            self.assertEqual(model_format(str(Path(d) / "m")), "skops")
            x = pd.DataFrame({"text": ["please refund the $40 charge from last week"]})
            self.assertEqual(list(load(str(Path(d) / "m")).predict(x)), ["billing"])

    def test_pickled_models_are_refused_before_they_are_opened(self):
        with tempfile.TemporaryDirectory() as d:
            for fmt in ("cloudpickle", "pickle"):
                path = Path(d) / fmt
                mlflow.sklearn.save_model(MODEL, str(path), serialization_format=fmt)
                with self.assertRaisesRegex(UnsafeModelError, f"stored as {fmt}"):
                    load(str(path))
