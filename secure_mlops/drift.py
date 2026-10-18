"""Drift check: does a batch of production tickets still look like the training data, and is the model still right?

Compares a current batch with the reference (the training split) on what the model sees and what it does:
- PSI of the channel and tier mix (who is writing, and how);
- PSI of the predicted teams (what the model decides);
- the rate of words never seen in training (new products, new phrasing);
- accuracy, when the batch carries labels from agents who re-routed tickets.
Each check is ok, warn or alert against configs/drift.yaml. Drift is a reason to look, not proof the model is worse:
on the generator, a fully shifted batch moves the channel PSI to 0.68 while accuracy only falls about two points.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import pandas as pd
import yaml

from .train import features

WORD = re.compile(r"[a-z]+")
LEVELS = ("ok", "warn", "alert")


@dataclass(frozen=True)
class DriftPolicy:
    psi_warn: float = 0.10
    psi_alert: float = 0.25
    new_word_rate_warn: float = 0.015
    new_word_rate_alert: float = 0.03
    max_accuracy_drop: float = 0.03
    min_batch: int = 200


def load_policy(path: str | Path) -> DriftPolicy:
    data = yaml.safe_load(Path(path).read_text()) or {}
    unknown = sorted(set(data) - {f.name for f in fields(DriftPolicy)})
    if unknown:
        raise ValueError(f"{path}: unknown drift key(s) {', '.join(unknown)}")
    return DriftPolicy(**data)


def psi(reference: Counter, current: Counter, floor: float = 1e-4) -> float:
    """Population stability index between two categorical distributions; empty bins get a small floor."""
    r_total, c_total = sum(reference.values()), sum(current.values())
    total = 0.0
    for key in set(reference) | set(current):
        r = max(reference.get(key, 0) / r_total, floor)
        c = max(current.get(key, 0) / c_total, floor)
        total += (c - r) * math.log(c / r)
    return total


def _words(texts) -> list[str]:
    return [w for t in texts for w in WORD.findall(t.lower())]


def _level(value: float, warn: float, alert: float) -> str:
    return "alert" if value > alert else "warn" if value > warn else "ok"


def check(model, reference: pd.DataFrame, current: pd.DataFrame, policy: DriftPolicy,
          reference_accuracy: float | None = None) -> dict:
    if len(current) < policy.min_batch:
        raise ValueError(f"batch has {len(current)} tickets; at least {policy.min_batch} are needed to judge drift")
    checks = []
    for column in ("channel", "tier"):
        value = psi(Counter(reference[column]), Counter(current[column]))
        checks.append({"name": f"psi[{column}]", "value": round(value, 4),
                       "level": _level(value, policy.psi_warn, policy.psi_alert)})
    pred_ref, pred_cur = Counter(model.predict(features(reference))), Counter(model.predict(features(current)))
    value = psi(pred_ref, pred_cur)
    checks.append({"name": "psi[predicted team]", "value": round(value, 4),
                   "level": _level(value, policy.psi_warn, policy.psi_alert)})
    vocab = set(_words(reference["text"]))
    words = _words(current["text"])
    rate = sum(w not in vocab for w in words) / max(1, len(words))
    checks.append({"name": "new word rate", "value": round(rate, 4),
                   "level": _level(rate, policy.new_word_rate_warn, policy.new_word_rate_alert)})
    if "label" in current and reference_accuracy is not None:
        accuracy = float((model.predict(features(current)) == current["label"]).mean())
        drop = reference_accuracy - accuracy
        checks.append({"name": "accuracy drop", "value": round(drop, 4),
                       "level": "alert" if drop > policy.max_accuracy_drop else "ok"})
    level = max((c["level"] for c in checks), key=LEVELS.index)
    return {"level": level, "batch": len(current), "checks": checks, "policy": asdict(policy),
            "retrain": level == "alert"}


def markdown(report: dict, model: str) -> str:
    mark = {"ok": "ok", "warn": "**warn**", "alert": "**ALERT**"}
    lines = [f"## Drift check: {model} on {report['batch']} tickets: {mark[report['level']]}", "",
             "Retraining recommended." if report["retrain"] else "No action needed.", "",
             "| Check | Value | Level |", "|---|---|---|"]
    lines += [f"| {c['name']} | {c['value']:.4f} | {mark[c['level']]} |" for c in report["checks"]]
    return "\n".join(lines) + "\n"
