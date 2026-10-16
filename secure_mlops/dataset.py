"""Splits and fingerprints: the same tickets always give the same train/validation/test sets and the same hash.

The fingerprint is a SHA-256 over a canonical form of the rows (sorted columns and rows, fixed encoding), so it
identifies the data itself, not the file it came from. Every run records it; a model can be traced to its data.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from sklearn.model_selection import train_test_split

COLUMNS = ["ticket_id", "text", "channel", "tier", "label"]


@dataclass(frozen=True)
class Splits:
    train: pd.DataFrame
    validation: pd.DataFrame
    test: pd.DataFrame

    def items(self):
        return (("train", self.train), ("validation", self.validation), ("test", self.test))


def fingerprint(frame: pd.DataFrame) -> str:
    canonical = frame[sorted(frame.columns)].sort_values(sorted(frame.columns)).to_csv(index=False, lineterminator="\n")
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def split(frame: pd.DataFrame, seed: int = 7, validation: float = 0.15, test: float = 0.15) -> Splits:
    """Stratified by label, so every split has every team in the same proportions."""
    if not 0 < validation < 1 or not 0 < test < 1 or validation + test >= 1:
        raise ValueError("validation and test shares must be between 0 and 1 and leave room for training")
    rest, test_df = train_test_split(frame, test_size=test, random_state=seed, stratify=frame["label"])
    train_df, val_df = train_test_split(rest, test_size=validation / (1 - test), random_state=seed,
                                        stratify=rest["label"])
    return Splits(*(d.sort_values("ticket_id").reset_index(drop=True) for d in (train_df, val_df, test_df)))


def text_overlap(splits: Splits) -> float:
    """Share of test tickets whose exact text also appears in training: a leakage indicator to record, since
    templated tickets repeat and an overlap inflates test scores."""
    seen = set(splits.train["text"])
    return float(splits.test["text"].isin(seen).mean()) if len(splits.test) else 0.0


def write(splits: Splits, directory: str | Path) -> dict[str, str]:
    """Write each split as CSV and return {split: fingerprint}."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    out = {}
    for name, frame in splits.items():
        frame[COLUMNS].to_csv(directory / f"{name}.csv", index=False, lineterminator="\n")
        out[name] = fingerprint(frame[COLUMNS])
    return out


def read(directory: str | Path, name: str) -> pd.DataFrame:
    return pd.read_csv(Path(directory) / f"{name}.csv", dtype=str, keep_default_na=False)
