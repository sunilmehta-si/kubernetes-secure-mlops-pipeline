"""Load models only in a format that cannot run code: skops, never pickle.

The format is read from the model's MLmodel metadata before anything is deserialised, so a pickled model, whether
an old one or one planted in the registry, is refused without being opened.
"""
from __future__ import annotations

from pathlib import Path

import mlflow
import mlflow.sklearn
import yaml

SAFE_FORMAT = "skops"


class UnsafeModelError(RuntimeError):
    pass


def _local_flavors(path: Path) -> dict:
    """Read a model directory's MLmodel file directly. mlflow.models.get_model_info would also look the model up in
    a tracking store, which a serving pod does not have."""
    meta = path / "MLmodel"
    if not meta.is_file():
        raise UnsafeModelError(f"{path} has no MLmodel file; refusing to load it")
    return (yaml.safe_load(meta.read_text()) or {}).get("flavors", {})


def model_format(uri: str) -> str:
    local = Path(uri)
    flavors = _local_flavors(local) if local.is_dir() else mlflow.models.get_model_info(uri).flavors
    if "sklearn" not in flavors:
        raise UnsafeModelError(f"{uri} is not a scikit-learn model; refusing to load it")
    return flavors["sklearn"].get("serialization_format", "cloudpickle")


def load(uri: str):
    fmt = model_format(uri)
    if fmt != SAFE_FORMAT:
        raise UnsafeModelError(f"{uri} is stored as {fmt}, which can run code when loaded; only {SAFE_FORMAT} is allowed")
    return mlflow.sklearn.load_model(uri)
