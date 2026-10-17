"""The Kubeflow Pipelines (v2) definition: prepare data -> train -> evaluate and gate -> publish.

Each component is a thin wrapper around a function in `steps.py`, so the pipeline and `smlops train` run the same
code. Components run in the project's own image (`SMLOPS_IMAGE`), which has this package installed. Artifacts are
typed (Dataset, Model, Metrics, Markdown), so the Kubeflow UI shows the data, the model, every metric and the gate
report for each run. Training and gate settings are pipeline parameters holding YAML, so a run records exactly
what it was asked to do.

No `from __future__ import annotations` here: KFP reads component signatures at import time and would take the
string annotations it produces for artifact types.
"""
import json
import os
from pathlib import Path

import yaml
from kfp import dsl

IMAGE = os.environ.get("SMLOPS_IMAGE", "ghcr.io/sunilmehta-si/kubernetes-secure-mlops-pipeline:dev")
ROOT = Path(__file__).resolve().parents[1]


def _default(name: str) -> str:
    """A committed config as one line of JSON (which YAML parsers read too). The KFP compiler copies defaults into
    a comment header, and a multi-line default breaks out of the comment and makes the compiled YAML invalid."""
    path = ROOT / "configs" / name
    return json.dumps(yaml.safe_load(path.read_text()), sort_keys=True) if path.exists() else "{}"


@dsl.component(base_image=IMAGE)
def prepare_data(n: int, seed: int, drift: float, data: dsl.Output[dsl.Dataset]):
    """Generate tickets, check them against the data contract, and write fingerprinted splits."""
    from secure_mlops.data import DataSpec
    from secure_mlops.steps import prepare

    manifest = prepare(DataSpec(n=n, seed=seed, drift=drift), data.path)   # raises, failing the run, on a breach
    data.metadata.update({"fingerprint_test": manifest["fingerprints"]["test"], "tickets": n})


@dsl.component(base_image=IMAGE)
def train_model(data: dsl.Input[dsl.Dataset], train_config: str, model_name: str, model: dsl.Output[dsl.Model]):
    """Train the classifier and hand it on in MLflow's model format."""
    from secure_mlops.config import parse
    from secure_mlops.steps import fit, save_model

    config = parse(train_config, "train_config parameter")
    save_model(fit(data.path, config, model_name), model.path)
    model.metadata.update({"params_hash": config.params_hash()})


@dsl.component(base_image=IMAGE)
def evaluate_and_gate(data: dsl.Input[dsl.Dataset], model: dsl.Input[dsl.Model], gate_policy: str, model_name: str,
                      tracking_uri: str, metrics: dsl.Output[dsl.Metrics], report: dsl.Output[dsl.Markdown],
                      decision: dsl.Output[dsl.Artifact]) -> bool:
    """Judge the challenger against the registered champion on the same test split."""
    import json

    import mlflow

    from secure_mlops.gate import parse_policy
    from secure_mlops.report import gate_markdown
    from secure_mlops.steps import judge, load_model
    from secure_mlops.tracking import flatten

    mlflow.set_tracking_uri(tracking_uri)
    evaluation, result = judge(load_model(model.path), data.path, parse_policy(gate_policy), model_name)
    for name, value in flatten(evaluation).items():
        metrics.log_metric(name, value)
    with open(report.path, "w") as f:
        f.write(gate_markdown(result, model_name))
    with open(decision.path, "w") as f:
        json.dump({"evaluation": evaluation, "decision": result}, f)
    return bool(result["passed"])


@dsl.component(base_image=IMAGE)
def publish_model(data: dsl.Input[dsl.Dataset], model: dsl.Input[dsl.Model], decision: dsl.Input[dsl.Artifact],
                  train_config: str, model_name: str, tracking_uri: str, card: dsl.Output[dsl.Markdown]) -> str:
    """Track and register every model; promote it to champion only if the gate passed. Returns the version."""
    import json
    import shutil
    import tempfile
    from pathlib import Path

    import mlflow

    from secure_mlops.config import parse
    from secure_mlops.steps import load_model, publish, validate_splits

    mlflow.set_tracking_uri(tracking_uri)
    judged = json.loads(Path(decision.path).read_text())
    with tempfile.TemporaryDirectory() as out:
        outcome = publish(load_model(model.path), parse(train_config), data.path, validate_splits(data.path),
                          judged["evaluation"], judged["decision"], model_name, out)
        shutil.copy(Path(out) / "model_card.md", card.path)
    return outcome.version


@dsl.pipeline(name="ticket-router-training",
              description="Prepare data, train, evaluate against the champion, and publish ticket-router models.")
def training_pipeline(n: int = 3000, seed: int = 7, drift: float = 0.0, train_config: str = _default("train.yaml"),
                      gate_policy: str = _default("gate.yaml"), model_name: str = "ticket-router",
                      tracking_uri: str = "http://mlflow.mlflow.svc.cluster.local:5000"):
    data = prepare_data(n=n, seed=seed, drift=drift)
    model = train_model(data=data.outputs["data"], train_config=train_config, model_name=model_name)
    gate = evaluate_and_gate(data=data.outputs["data"], model=model.outputs["model"], gate_policy=gate_policy,
                             model_name=model_name, tracking_uri=tracking_uri)
    publish_model(data=data.outputs["data"], model=model.outputs["model"], decision=gate.outputs["decision"],
                  train_config=train_config, model_name=model_name, tracking_uri=tracking_uri)
