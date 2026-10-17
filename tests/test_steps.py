import json
import os
import tempfile
import unittest
from pathlib import Path

import mlflow
import pandas as pd

from secure_mlops import registry
from secure_mlops.config import TrainConfig
from secure_mlops.data import DataSpec, generate
from secure_mlops.dataset import split, write
from secure_mlops.gate import GatePolicy
from secure_mlops.steps import StepError, train_and_gate

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")


class StepsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        mlflow.set_tracking_uri(f"sqlite:///{self.root}/mlflow.db")
        write(split(pd.DataFrame(generate(DataSpec(n=3000)))), self.root / "data")

    def tearDown(self):
        mlflow.set_tracking_uri("")
        self.dir.cleanup()

    def run_once(self, config=TrainConfig()):
        return train_and_gate(self.root / "data", config, GatePolicy(), "router", self.root / "out")

    def test_first_model_is_promoted_then_a_worse_one_is_blocked(self):
        first = self.run_once()
        self.assertEqual((first.version, first.promoted), ("1", True))
        worse = self.run_once(TrainConfig(c=0.05, min_df=5))
        self.assertEqual((worse.version, worse.promoted, worse.passed), ("2", False, False))
        self.assertIn("drop in accuracy", worse.failed_checks)
        self.assertEqual(registry.by_alias("router", registry.CHAMPION).version, "1")
        gate = json.loads((self.root / "out/gate.json").read_text())
        self.assertEqual((gate["compared_with_champion"], gate["champion_version"]), (True, "1"))
        self.assertIn("router v2 **failed**", (self.root / "out/gate.md").read_text())
        card = (self.root / "out/model_card.md").read_text()
        self.assertIn("# Model card: router v2", card)
        self.assertIn("Evaluation gate: failed: ", card)
        self.assertIn("against champion v1", card)
        self.assertIn("| security |", card)
        self.assertIn("synthetic tickets", card)
        self.assertRegex(card, r"- Code: commit `([0-9a-f]{40}|unknown)`")
        self.assertIn("packages `sha256:", card)
        run = mlflow.get_run(worse.run_id)
        self.assertIn("model_card.md", [a.path for a in mlflow.MlflowClient().list_artifacts(run.info.run_id)])

    def test_a_model_survives_the_hand_off_between_steps(self):
        from secure_mlops.steps import fit, load_model, save_model
        model = fit(self.root / "data", TrainConfig())
        save_model(model, self.root / "model")
        x = pd.DataFrame({"text": ["my account is locked after too many attempts"]})
        self.assertEqual(list(load_model(self.root / "model").predict(x)), list(model.predict(x)))
        files = {p.name for p in (self.root / "model").iterdir()}
        self.assertIn("model.skops", files)                           # never a pickle
        self.assertFalse({f for f in files if f.endswith((".pkl", ".pickle"))})

    def test_bad_data_stops_before_training(self):
        test = self.root / "data/test.csv"
        test.write_text(test.read_text().replace(",email,", ",fax,", 1))
        with self.assertRaisesRegex(StepError, "test split failed the data contract"):
            self.run_once()
        self.assertIsNone(registry.by_alias("router", registry.CHALLENGER))      # nothing was registered
