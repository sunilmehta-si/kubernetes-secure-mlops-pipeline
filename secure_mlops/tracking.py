"""Record a training run in MLflow: what went in, what came out, and what the gate decided.

Each run carries enough to reproduce and audit it: the exact parameters and their hash, the fingerprints of the
data splits, the code version, every metric (including slices), the gate decision and its report, and the model
with its input signature, so the registry knows what a request to it must look like.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

import mlflow
import mlflow.sklearn
import pandas as pd
from mlflow.models import infer_signature

from . import __version__
from .config import TrainConfig
from .provenance import environment_info, frozen_requirements, git_info
from .report import gate_markdown
from .safeload import SAFE_FORMAT  # models are stored with skops, never pickle
from .train import features




@dataclass(frozen=True)
class LoggedRun:
    run_id: str
    model_uri: str


def metric_name(name: str) -> str:
    """MLflow metric names allow letters, digits and _-. /; slice names such as channel=chat are mapped onto that."""
    return re.sub(r"[^0-9A-Za-z_\-. /]", "_", name)


def flatten(evaluation: dict) -> dict[str, float]:
    out = {k: evaluation[k] for k in ("accuracy", "macro_f1", "calibration_error")}
    out.update({f"recall.{lab}": v for lab, v in evaluation["recall_by_label"].items()})
    out.update({metric_name(f"slice.{name}.macro_f1"): s["macro_f1"] for name, s in evaluation["slices"].items()})
    if "latency_ms" in evaluation:
        out.update({f"latency_ms.{k}": v for k, v in evaluation["latency_ms"].items()})
    return out


def log_run(model, config: TrainConfig, train_frame: pd.DataFrame, test_frame: pd.DataFrame, fingerprints: dict,
            evaluation: dict, decision: dict, experiment: str = "ticket-router") -> LoggedRun:
    mlflow.set_experiment(experiment)
    with mlflow.start_run() as run:
        mlflow.log_params(asdict(config))
        mlflow.set_tags({**git_info(), **environment_info(), "params_hash": config.params_hash(),
                         "code_version": __version__,
                         "gate": "passed" if decision["passed"] else "failed",
                         **{f"data.{name}": fp for name, fp in sorted(fingerprints.items())}})
        for frame, context in ((train_frame, "training"), (test_frame, "evaluation")):
            mlflow.log_input(mlflow.data.from_pandas(frame, name=f"tickets-{context}", targets="label"), context=context)
        mlflow.log_metrics(flatten(evaluation))
        mlflow.log_text(frozen_requirements(), "environment/requirements-frozen.txt")
        mlflow.log_dict(evaluation, "evaluation.json")
        mlflow.log_dict(decision, "gate.json")
        mlflow.log_text(gate_markdown(decision), "gate.md")
        example = features(test_frame).head(3)
        info = mlflow.sklearn.log_model(model, name="model", signature=infer_signature(example, model.predict(example)),
                                        input_example=example, serialization_format=SAFE_FORMAT)
    return LoggedRun(run.info.run_id, info.model_uri)
