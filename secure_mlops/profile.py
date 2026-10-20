"""The reference profile a model carries: what its training traffic looked like.

Stored as MLflow model metadata, so it sits in the model's MLmodel file and is covered by the model's signature: the
server compares live traffic with the profile of exactly the model it serves, and the profile cannot be swapped
without breaking verification. Today it holds the share of tickets per team; the vocabulary comes from the model.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import yaml

KEY = "smlops_reference"


def reference_profile(train_frame: pd.DataFrame) -> dict:
    shares = train_frame["label"].value_counts(normalize=True).sort_index()
    return {"tickets": int(len(train_frame)), "teams": {str(k): round(float(v), 6) for k, v in shares.items()}}


def read_profile(model_dir: str | Path) -> dict | None:
    meta = Path(model_dir) / "MLmodel"
    if not meta.is_file():
        return None
    return ((yaml.safe_load(meta.read_text()) or {}).get("metadata") or {}).get(KEY)
