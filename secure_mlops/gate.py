"""The evaluation gate: may this challenger replace the champion?

Two kinds of checks, all evaluated and all reported:
- floors: absolute minimums any production model must meet (accuracy, macro F1, every team's recall,
  calibration, latency);
- no regression: against the current champion on the same test set, overall, per team and per slice. A slice
  only counts when it has enough tickets to be more than noise.
The first model has no champion and is judged on floors alone.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from pathlib import Path

import yaml


@dataclass(frozen=True)
class GatePolicy:
    min_accuracy: float = 0.90
    min_macro_f1: float = 0.90
    min_recall_per_label: float = 0.85
    max_calibration_error: float = 0.10
    max_latency_p95_ms: float = 25.0
    max_drop_overall: float = 0.01          # accuracy and macro F1, absolute points
    max_drop_per_label: float = 0.03        # recall of any one team
    max_drop_per_slice: float = 0.03        # macro F1 of any slice with at least min_slice_size tickets
    min_slice_size: int = 30


@dataclass(frozen=True)
class Check:
    name: str
    value: float
    limit: float
    passed: bool
    kind: str                               # "floor" or "regression"


def parse_policy(text: str, source: str = "gate policy") -> GatePolicy:
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{source}: expected a mapping of gate settings")
    known = {f.name for f in fields(GatePolicy)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ValueError(f"{source}: unknown gate key(s) {', '.join(unknown)}")
    return GatePolicy(**data)


def load_policy(path: str | Path) -> GatePolicy:
    return parse_policy(Path(path).read_text(), str(path))


def _floor(name, value, limit, higher_is_better=True) -> Check:
    return Check(name, value, limit, value >= limit if higher_is_better else value <= limit, "floor")


def _drop(name, challenger, champion, max_drop) -> Check:
    drop = round(champion - challenger, 4)
    return Check(name, drop, max_drop, drop <= max_drop, "regression")


def decide(challenger: dict, champion: dict | None, policy: GatePolicy) -> dict:
    checks = [_floor("accuracy", challenger["accuracy"], policy.min_accuracy),
              _floor("macro_f1", challenger["macro_f1"], policy.min_macro_f1),
              _floor("calibration_error", challenger["calibration_error"], policy.max_calibration_error, False)]
    checks += [_floor(f"recall[{lab}]", r, policy.min_recall_per_label) for lab, r in challenger["recall_by_label"].items()]
    if "latency_ms" in challenger:
        checks.append(_floor("latency_p95_ms", challenger["latency_ms"]["p95"], policy.max_latency_p95_ms, False))
    if champion is not None:
        if champion.get("n") != challenger.get("n"):
            raise ValueError("challenger and champion must be evaluated on the same test set")
        checks += [_drop(f"drop in {m}", challenger[m], champion[m], policy.max_drop_overall) for m in ("accuracy", "macro_f1")]
        checks += [_drop(f"drop in recall[{lab}]", challenger["recall_by_label"][lab], champion["recall_by_label"][lab],
                         policy.max_drop_per_label) for lab in sorted(challenger["recall_by_label"])]
        for name, s in sorted(challenger["slices"].items()):
            if s["n"] >= policy.min_slice_size and name in champion["slices"]:
                checks.append(_drop(f"drop in macro_f1[{name}]", s["macro_f1"], champion["slices"][name]["macro_f1"],
                                    policy.max_drop_per_slice))
    failed = [c.name for c in checks if not c.passed]
    return {"passed": not failed, "failed": failed, "compared_with_champion": champion is not None,
            "policy": asdict(policy), "checks": [asdict(c) for c in checks]}
