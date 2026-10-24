import os
import tempfile
import unittest
from pathlib import Path

import yaml
from kfp import compiler

from secure_mlops.pipeline import training_pipeline

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
ROOT = Path(__file__).resolve().parents[1]


def compiled() -> dict:
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "p.yaml"
        compiler.Compiler().compile(training_pipeline, str(path))
        return yaml.safe_load(path.read_text())


class PipelineSpecTests(unittest.TestCase):
    def test_four_steps_in_order_with_typed_artifacts(self):
        spec = compiled()
        tasks = spec["root"]["dag"]["tasks"]
        self.assertEqual(set(tasks), {"prepare-data", "train-model", "evaluate-and-gate", "publish-model"})
        self.assertEqual(tasks["train-model"]["dependentTasks"], ["prepare-data"])
        self.assertEqual(sorted(tasks["publish-model"]["dependentTasks"]), ["evaluate-and-gate", "prepare-data", "train-model"])
        outputs = spec["components"]["comp-evaluate-and-gate"]["outputDefinitions"]["artifacts"]
        self.assertEqual(outputs["metrics"]["artifactType"]["schemaTitle"], "system.Metrics")
        self.assertEqual(outputs["report"]["artifactType"]["schemaTitle"], "system.Markdown")

    def test_configs_are_parameters_with_the_committed_defaults(self):
        params = compiled()["root"]["inputDefinitions"]["parameters"]
        from secure_mlops.config import load, parse
        from secure_mlops.gate import load_policy, parse_policy
        self.assertEqual(parse(params["train_config"]["defaultValue"]), load(ROOT / "configs/train.yaml"))
        self.assertEqual(parse_policy(params["gate_policy"]["defaultValue"]), load_policy(ROOT / "configs/gate.yaml"))
        self.assertNotIn("\n", params["train_config"]["defaultValue"])      # multi-line defaults break the YAML
        self.assertEqual(params["n"]["defaultValue"], 3000)

    def test_components_run_in_the_project_image(self):
        images = {e["container"]["image"] for e in compiled()["deploymentSpec"]["executors"].values()}
        self.assertEqual(len(images), 1)
        self.assertTrue(images.pop().startswith("ghcr.io/sunilmehta-si/kubernetes-secure-mlops-pipeline"))


class CompiledPipelineTests(unittest.TestCase):
    def test_committed_pipeline_matches_the_code(self):
        import io
        from contextlib import redirect_stderr

        from secure_mlops.cli import main
        with tempfile.TemporaryDirectory() as d, redirect_stderr(io.StringIO()):
            self.assertEqual(main(["pipeline", "compile", "-o", f"{d}/p.yaml"]), 0)
            fresh = Path(d, "p.yaml").read_text()
        self.assertEqual((ROOT / "pipelines/ticket-router-training.yaml").read_text(), fresh,
                         "pipelines/ticket-router-training.yaml is stale: run smlops pipeline compile")
        yaml.safe_load(fresh)                                           # valid YAML, as Kubeflow will parse it

    def test_components_install_nothing_at_run_time(self):
        # kfp's default component command pip-installs kfp before the step runs. The project image already holds
        # the locked kfp, and an environment built from the lock has no pip: the CI run found every step failing.
        self.assertNotIn("pip install", (ROOT / "pipelines/ticket-router-training.yaml").read_text())


class LocalRunTests(unittest.TestCase):
    def test_pipeline_runs_end_to_end_and_gates_the_second_model(self):
        import io
        from contextlib import redirect_stderr, redirect_stdout

        import mlflow

        from secure_mlops import registry
        from secure_mlops.cli import main
        with tempfile.TemporaryDirectory() as d, redirect_stderr(io.StringIO()) as err, redirect_stdout(io.StringIO()):
            uri = f"sqlite:///{d}/mlflow.db"
            # the default 3000 tickets: on 1200 the test split is small enough that calibration error lands just
            # over the gate's 0.10 floor, and the gate (rightly) holds the model back
            common = ["pipeline", "run-local", "--n", "3000", "--tracking-uri", uri, "-o", f"{d}/runs",
                      "--gate", str(ROOT / "configs/gate.yaml")]
            try:
                self.assertEqual(main([*common, "--config", str(ROOT / "configs/train.yaml")]), 0)
                self.assertIn("v1 is the new champion", err.getvalue())
                weak = Path(d, "weak.yaml")
                weak.write_text("c: 0.05\nmin_df: 5\n")
                self.assertEqual(main([*common, "--config", str(weak)]), 1)
                self.assertIn("v2 was registered but not promoted (champion: v1)", err.getvalue())
                mlflow.set_tracking_uri(uri)
                self.assertEqual(registry.by_alias("ticket-router", registry.CHAMPION).version, "1")
                cards = list(Path(d, "runs").rglob("card"))
                self.assertEqual(len(cards), 2)                         # one model card artifact per run
            finally:
                mlflow.set_tracking_uri("")
