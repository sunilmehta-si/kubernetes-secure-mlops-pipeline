"""The pipeline's steps as plain functions: the CLI and the Kubeflow components call the same code.

validate -> train -> evaluate -> judge against the champion -> track -> register -> promote. A step that fails
raises StepError with a reason, and nothing after it runs: no unvalidated data is trained on, and no model is
promoted without a gate decision.
"""
from __future__ import annotations

import json
import os
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
from .provenance import environment_info, git_info
from .risk import RiskError, require_current
from .safeload import SAFE_FORMAT, load as safe_load
from .signing import SignatureError
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


def prepare(spec, data_dir: str | Path) -> dict:
    """Generate tickets, check the whole set and then each split against the data contract, write the splits and a
    manifest of their fingerprints. The CLI and the pipeline both call this, so both apply the same contract."""
    import pandas as pd

    from .data import generate
    from .dataset import split, text_overlap, write
    frame = pd.DataFrame(generate(spec))
    report = validate(frame)
    if not report.ok:
        raise StepError("generated data failed the data contract: " + "; ".join(report.problems))
    splits = split(frame, seed=spec.seed)
    fingerprints = write(splits, data_dir)
    validate_splits(data_dir)
    manifest = {"spec": {"n": spec.n, "seed": spec.seed, "drift": spec.drift, "label_noise": spec.label_noise,
                         "typo_rate": spec.typo_rate},
                "fingerprints": fingerprints, "rows": {name: len(d) for name, d in splits.items()},
                "test_text_overlap": round(text_overlap(splits), 4)}
    (Path(data_dir) / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


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


def fit(data_dir: str | Path, config: TrainConfig, model_name: str):
    """Gate G0 comes first: no model is trained for a use case without a current risk register entry."""
    try:
        require_current(model_name)
    except RiskError as exc:
        raise StepError(str(exc)) from exc
    return train(read(data_dir, "train"), config)


def judge(model, data_dir: str | Path, policy: GatePolicy, model_name: str,
          public_key: str | Path | None = None) -> tuple[dict, dict]:
    """Evaluate the challenger and the current champion on the same test split, and decide. With a verification
    key, a champion whose files no longer match its signature stops the run: a tampered baseline is never trusted."""
    test_frame = read(data_dir, "test")
    challenger = evaluate(model, test_frame)
    current = registry.by_alias(model_name, registry.CHAMPION)
    try:
        champion = evaluate(registry.load(current, public_key), test_frame) if current else None
    except SignatureError as exc:
        raise StepError(f"champion {model_name} v{current.version} failed verification: {exc}") from exc
    decision = decide(challenger, champion, policy)
    if current:
        decision["champion_version"] = current.version
    return challenger, decision


def signing_key(explicit: str | Path | None = None) -> Path | None:
    """The private key to sign models with: an explicit path, or SMLOPS_SIGNING_KEY (a mounted secret in a cluster)."""
    value = explicit or os.environ.get("SMLOPS_SIGNING_KEY")
    return Path(value) if value else None


def publish(model, config: TrainConfig, data_dir: str | Path, fingerprints: dict, evaluation: dict, decision: dict,
            model_name: str, output_dir: str | Path, private_key: str | Path | None = None) -> Outcome:
    """Track the run, sign the stored model when a key is configured, register it, promote it if the gate passed,
    and write the reports. An unsigned version is tagged as such, never left ambiguous."""
    train_frame, test_frame = read(data_dir, "train"), read(data_dir, "test")
    logged = log_run(model, config, train_frame, test_frame, fingerprints, evaluation, decision, experiment=model_name)
    key = signing_key(private_key)
    signature = registry.sign_stored_model(logged.model_uri, logged.run_id, key) if key else ""
    version = registry.register(logged.model_uri, model_name, {"params_hash": config.params_hash(),
                                                                "data.test": fingerprints["test"],
                                                                "run_id": logged.run_id,
                                                                "signature": signature or "unsigned"})
    promoted = registry.promote(version, decision)
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "release.json").write_text(json.dumps({"name": model_name, "version": version.version,
                                                   "run_id": logged.run_id, "promoted": promoted}, indent=2) + "\n")
    (out / "evaluation.json").write_text(json.dumps(evaluation, indent=2) + "\n")
    (out / "gate.json").write_text(json.dumps(decision, indent=2) + "\n")
    (out / "gate.md").write_text(gate_markdown(decision, f"{model_name} v{version.version}"))
    card = model_card(model_name, version.version, config, fingerprints, evaluation, decision,
                      provenance={**git_info(), **environment_info()}, risk=require_current(model_name))
    (out / "model_card.md").write_text(card)
    mlflow.MlflowClient().log_text(logged.run_id, card, "model_card.md")
    return Outcome(logged.run_id, version.version, promoted, decision["passed"], decision["failed"])


def save_model(model, path: str | Path) -> None:
    """Hand a model to the next pipeline step in MLflow's model format."""
    mlflow.sklearn.save_model(model, str(path), serialization_format=SAFE_FORMAT)


def load_model(path: str | Path):
    return safe_load(str(path))


def train_and_gate(data_dir: str | Path, config: TrainConfig, policy: GatePolicy, model_name: str,
                   output_dir: str | Path, private_key: str | Path | None = None,
                   public_key: str | Path | None = None) -> Outcome:
    fingerprints = validate_splits(data_dir)
    model = fit(data_dir, config, model_name)
    evaluation, decision = judge(model, data_dir, policy, model_name, public_key)
    return publish(model, config, data_dir, fingerprints, evaluation, decision, model_name, output_dir, private_key)
