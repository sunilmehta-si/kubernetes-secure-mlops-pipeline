import os
import tempfile
import unittest

import mlflow
import mlflow.sklearn
import pandas as pd

from secure_mlops import registry
from secure_mlops.config import TrainConfig
from secure_mlops.data import DataSpec, generate
from secure_mlops.dataset import split
from secure_mlops.train import train

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
SPLITS = split(pd.DataFrame(generate(DataSpec(n=600))))


def logged_model():
    with mlflow.start_run():
        return mlflow.sklearn.log_model(train(SPLITS.train, TrainConfig()), name="model").model_uri


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        mlflow.set_tracking_uri(f"sqlite:///{self.dir.name}/mlflow.db")
        mlflow.set_experiment("registry-test")       # MLflow caches the active experiment across tracking URIs

    def tearDown(self):
        mlflow.set_tracking_uri("")
        self.dir.cleanup()

    def test_only_a_gate_pass_moves_the_champion_and_rollback_restores_it(self):
        self.assertIsNone(registry.by_alias("router", registry.CHAMPION))
        v1 = registry.register(logged_model(), "router", {"params_hash": "x"})
        self.assertEqual(registry.by_alias("router", registry.CHALLENGER), v1)
        self.assertTrue(registry.promote(v1, {"passed": True}))
        v2 = registry.register(logged_model(), "router", {})
        self.assertFalse(registry.promote(v2, {"passed": False}))
        self.assertEqual(registry.by_alias("router", registry.CHAMPION), v1)        # a failed gate changes nothing
        v3 = registry.register(logged_model(), "router", {})
        self.assertTrue(registry.promote(v3, {"passed": True}))
        self.assertEqual((registry.by_alias("router", registry.CHAMPION), registry.by_alias("router", registry.PREVIOUS)),
                         (v3, v1))
        self.assertEqual(registry.rollback("router"), v1)
        self.assertEqual(registry.by_alias("router", registry.CHAMPION), v1)
        with self.assertRaisesRegex(ValueError, "no previous champion"):
            registry.rollback("router")
        self.assertEqual(list(registry.load(v1).predict(SPLITS.test[["text"]].head(1))), [registry.load(v3).predict(
            SPLITS.test[["text"]].head(1))[0]])

    def test_retiring_removes_every_release_alias_and_blocks_promotion(self):
        v1 = registry.register(logged_model(), "router", {})
        registry.promote(v1, {"passed": True})
        v2 = registry.register(logged_model(), "router", {})
        registry.promote(v2, {"passed": True})
        self.assertIsNone(registry.retired("router"))
        with self.assertRaisesRegex(ValueError, "a reason and who"):
            registry.retire("router", " ", "on-call")
        self.assertEqual(registry.retire("router", "replaced by a vendor router", "@owner"),
                         ["champion", "challenger", "previous"])
        for alias in (registry.CHAMPION, registry.CHALLENGER, registry.PREVIOUS):
            self.assertIsNone(registry.by_alias("router", alias))
        record = registry.retired("router")
        self.assertEqual((record["reason"], record["by"]), ("replaced by a vendor router", "@owner"))
        self.assertRegex(record["at"], r"^\d{4}-\d{2}-\d{2}T")
        with self.assertRaisesRegex(ValueError, "retired"):
            registry.promote(v2, {"passed": True})
        self.assertIsNotNone(registry.load(v1))                            # the versions stay, for audit
