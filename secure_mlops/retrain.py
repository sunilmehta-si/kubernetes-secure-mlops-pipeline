"""Retraining (gate G7 to gate G3): when live drift alerts, train again on what the model already knew plus what
traffic has become, and let the evaluation gate decide.

- combine(): the data for a retrain. Train and validation are the existing data plus recent labelled tickets; test is
  recent tickets only, so champion and challenger are judged on today's traffic. Recent test texts are removed from
  the training side, so the comparison cannot be inflated by leakage. Recent tickets alone are not enough: on the
  generator, a model trained only on shifted tickets lost 6.6 points of account recall and the gate blocked it;
  trained on both it scored 0.964 against the champion's 0.942 on the same recent tickets.
- retrain_reasons(): reads an Alertmanager webhook payload and returns the firing alerts that call for retraining
  (label action=retrain). Anything else, resolved alerts included, starts nothing.

Retraining never promotes on its own authority: the gate judges the challenger against the champion, and a person
approves promotion (gate G4).
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .dataset import Splits, read, write
from .steps import StepError, validate_splits


def combine(base_dir: str | Path, recent_dir: str | Path, out_dir: str | Path) -> dict:
    base = {name: read(base_dir, name) for name in ("train", "validation")}
    recent = {name: read(recent_dir, name) for name in ("train", "validation", "test")}
    held_out = set(recent["test"]["text"])
    sides = {}
    for name in ("train", "validation"):
        frame = pd.concat([base[name], recent[name]], ignore_index=True)
        if frame["ticket_id"].duplicated().any():
            raise StepError(f"{name}: the base and recent data share ticket ids; they must be different tickets")
        sides[name] = frame[~frame["text"].isin(held_out)].sort_values("ticket_id").reset_index(drop=True)
    fingerprints = write(Splits(sides["train"], sides["validation"], recent["test"]), out_dir)
    validate_splits(out_dir)
    manifest = {"combined": {"base": str(base_dir), "recent": str(recent_dir),
                             "removed_for_overlap_with_test": {n: len(base[n]) + len(recent[n]) - len(sides[n])
                                                               for n in sides}},
                "fingerprints": fingerprints,
                "rows": {"train": len(sides["train"]), "validation": len(sides["validation"]),
                         "test": len(recent["test"])}}
    (Path(out_dir) / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def retrain_reasons(payload: dict) -> list[str]:
    """Firing alerts labelled action=retrain, as 'AlertName: summary', from an Alertmanager webhook payload."""
    reasons = []
    for alert in payload.get("alerts", []):
        labels = alert.get("labels", {})
        if alert.get("status") == "firing" and labels.get("action") == "retrain":
            summary = alert.get("annotations", {}).get("summary", "")
            reasons.append(f"{labels.get('alertname', 'unnamed alert')}: {summary}".rstrip(": "))
    return sorted(set(reasons))
