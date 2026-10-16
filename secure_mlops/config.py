"""Training configuration: a small YAML file, validated, with a hash that identifies the exact parameters.

Unknown keys are errors rather than silently ignored: a typo such as `max_featurs` would otherwise train a
different model than the one the reviewer approved.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import yaml


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class TrainConfig:
    ngram_max: int = 2              # word n-grams from 1 to this
    min_df: int = 2                 # ignore terms seen in fewer tickets
    max_features: int = 20000
    sublinear_tf: bool = True
    c: float = 4.0                  # inverse regularisation strength of the logistic regression
    class_weight: str | None = "balanced"
    max_iter: int = 2000
    seed: int = 7

    def __post_init__(self):
        if self.ngram_max < 1 or self.min_df < 1 or self.max_features < 1 or self.max_iter < 1:
            raise ConfigError("ngram_max, min_df, max_features and max_iter must be at least 1")
        if self.c <= 0:
            raise ConfigError("c must be > 0")
        if self.class_weight not in (None, "balanced"):
            raise ConfigError("class_weight must be null or 'balanced'")

    def params_hash(self) -> str:
        canonical = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def load(path: str | Path) -> TrainConfig:
    data = yaml.safe_load(Path(path).read_text()) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a mapping of training parameters")
    known = {f.name for f in fields(TrainConfig)}
    unknown = sorted(set(data) - known)
    if unknown:
        raise ConfigError(f"{path}: unknown key(s) {', '.join(unknown)}; known: {', '.join(sorted(known))}")
    try:
        return TrainConfig(**data)
    except TypeError as exc:
        raise ConfigError(f"{path}: {exc}") from exc
