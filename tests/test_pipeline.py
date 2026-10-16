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
