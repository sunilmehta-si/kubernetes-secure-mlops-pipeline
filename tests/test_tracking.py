import os
import tempfile
import unittest

import mlflow
import pandas as pd

from secure_mlops.config import TrainConfig
from secure_mlops.data import DataSpec, generate
from secure_mlops.dataset import fingerprint, split
from secure_mlops.evaluate import evaluate
from secure_mlops.gate import GatePolicy, decide
from secure_mlops.tracking import flatten, log_run, metric_name
from secure_mlops.train import train

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")


class TrackingTests(unittest.TestCase):
    def test_metric_names_are_mlflow_safe(self):
        self.assertEqual(metric_name("slice.channel=chat.macro_f1"), "slice.channel_chat.macro_f1")

    def test_run_records_inputs_outputs_and_a_servable_model(self):
        s = split(pd.DataFrame(generate(DataSpec(n=800))))
        model = train(s.train, TrainConfig())
        ev = evaluate(model, s.test, measure_latency=False)
        decision = decide(ev, None, GatePolicy())
        with tempfile.TemporaryDirectory() as d:
            mlflow.set_tracking_uri(f"sqlite:///{d}/mlflow.db")
            try:
                logged = log_run(model, TrainConfig(), s.train, s.test, {"test": fingerprint(s.test)}, ev, decision,
                                 experiment="test")
                run = mlflow.get_run(logged.run_id)
                self.assertEqual(run.data.tags["params_hash"], TrainConfig().params_hash())
                self.assertEqual(run.data.tags["data.test"], fingerprint(s.test))
                self.assertEqual(run.data.metrics["accuracy"], ev["accuracy"])
                self.assertEqual(set(flatten(ev)) - set(run.data.metrics), set())
                served = mlflow.pyfunc.load_model(logged.model_uri)
                ticket = pd.DataFrame({"text": ["please refund the $40 charge from last week"]})
                self.assertEqual(list(served.predict(ticket)), ["billing"])
            finally:
                mlflow.set_tracking_uri("")
