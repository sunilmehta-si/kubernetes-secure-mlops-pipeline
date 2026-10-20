"""The reference profile a model carries: what its training traffic looked like.

Stored as MLflow model metadata, so it sits in the model's MLmodel file and is covered by the model's signature: the
server compares live traffic with the profile of exactly the model it serves, and the profile cannot be swapped
without breaking verification. It holds the share of tickets per team in training, and the model's own new-word rate
on held-out tickets: the vectorizer drops rare words, so even training-like text has some, and live traffic is judged
by how far it rises above this baseline rather than against zero.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

KEY = "smlops_reference"


def reference_profile(train_frame: pd.DataFrame, model=None, heldout: pd.DataFrame | None = None) -> dict:
    shares = train_frame["label"].value_counts(normalize=True).sort_index()
    profile = {"tickets": int(len(train_frame)), "teams": {str(k): round(float(v), 6) for k, v in shares.items()}}
    if model is not None and heldout is not None:
        from .serve.live_drift import model_vocabulary, new_word_rate
        profile["new_word_rate"] = round(new_word_rate(heldout["text"], model_vocabulary(model)), 6)
    return profile


def read_profile(model_dir: str | Path) -> dict | None:
    meta = Path(model_dir) / "MLmodel"
    if not meta.is_file():
        return None
    return ((yaml.safe_load(meta.read_text()) or {}).get("metadata") or {}).get(KEY)
