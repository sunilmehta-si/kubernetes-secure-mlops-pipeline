"""The pipeline's steps as plain functions: the CLI and the Kubeflow components call the same code.

validate -> train -> evaluate -> judge against the champion -> track -> register -> promote. A step that fails
raises StepError with a reason, and nothing after it runs: no unvalidated data is trained on, and no model is
promoted without a gate decision.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import mlflow
import mlflow.sklearn

from . import registry
from .card import model_card
from .config import TrainConfig
from .dataset import fingerprint, read
from .evaluate import evaluate
from .gate import GatePolicy, decide
from .report import gate_markdown
from .tracking import log_run
from .train import train
from .validate import validate


class StepError(RuntimeError):
    pass


@dataclass(frozen=True)
class Outcome:
    run_id: str
    version: str
    promoted: bool
    passed: bool
    failed_checks: list[str]


def validate_splits(data_dir: str | Path) -> dict[str, str]:
    """Every split must pass the data contract; returns their fingerprints."""
    prints = {}
    for name in ("train", "validation", "test"):
        frame = read(data_dir, name)
        report = validate(frame)
        if not report.ok:
            raise StepError(f"{name} split failed the data contract: " + "; ".join(report.problems))
        prints[name] = fingerprint(frame)
    return prints


def fit(data_dir: str | Path, config: TrainConfig):
    return train(read(data_dir, "train"), config)


def judge(model, data_dir: str | Path, policy: GatePolicy, model_name: str) -> tuple[dict, dict]:
    """Evaluate the challenger and the current champion on the same test split, and decide."""
    test_frame = read(data_dir, "test")
    challenger = evaluate(model, test_frame)
    current = registry.by_alias(model_name, registry.CHAMPION)
    champion = evaluate(registry.load(current), test_frame) if current else None
    decision = decide(challenger, champion, policy)
    if current:
        decision["champion_version"] = current.version
    return challenger, decision


def publish(model, config: TrainConfig, data_dir: str | Path, fingerprints: dict, evaluation: dict, decision: dict,
            model_name: str, output_dir: str | Path) -> Outcome:
    """Track the run, register the model, promote it if the gate passed, and write the reports."""
    train_frame, test_frame = read(data_dir, "train"), read(data_dir, "test")
    logged = log_run(model, config, train_frame, test_frame, fingerprints, evaluation, decision, experiment=model_name)
    version = registry.register(logged.model_uri, model_name, {"params_hash": config.params_hash(),
                                                                "data.test": fingerprints["test"],
                                                                "run_id": logged.run_id})
    promoted = registry.promote(version, decision)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "evaluation.json").write_text(json.dumps(evaluation, indent=2) + "\n")
    (out / "gate.json").write_text(json.dumps(decision, indent=2) + "\n")
    (out / "gate.md").write_text(gate_markdown(decision, f"{model_name} v{version.version}"))
    card = model_card(model_name, version.version, config, fingerprints, evaluation, decision)
    (out / "model_card.md").write_text(card)
    mlflow.MlflowClient().log_text(logged.run_id, card, "model_card.md")
    return Outcome(logged.run_id, version.version, promoted, decision["passed"], decision["failed"])


def save_model(model, path: str | Path) -> None:
    """Hand a model to the next pipeline step in MLflow's model format."""
    mlflow.sklearn.save_model(model, str(path))


def load_model(path: str | Path):
    return mlflow.sklearn.load_model(str(path))


def train_and_gate(data_dir: str | Path, config: TrainConfig, policy: GatePolicy, model_name: str,
                   output_dir: str | Path) -> Outcome:
    fingerprints = validate_splits(data_dir)
    model = fit(data_dir, config)
    evaluation, decision = judge(model, data_dir, policy, model_name)
    return publish(model, config, data_dir, fingerprints, evaluation, decision, model_name, output_dir)
