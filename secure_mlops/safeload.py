"""Load models only in a format that cannot run code: skops, never pickle.

The format is read from the model's MLmodel metadata before anything is deserialised, so a pickled model, whether
an old one or one planted in the registry, is refused without being opened.
"""
from __future__ import annotations

import mlflow
import mlflow.sklearn

SAFE_FORMAT = "skops"


class UnsafeModelError(RuntimeError):
    pass


def model_format(uri: str) -> str:
    flavors = mlflow.models.get_model_info(uri).flavors
    if "sklearn" not in flavors:
        raise UnsafeModelError(f"{uri} is not a scikit-learn model; refusing to load it")
    return flavors["sklearn"].get("serialization_format", "cloudpickle")


def load(uri: str):
    fmt = model_format(uri)
    if fmt != SAFE_FORMAT:
        raise UnsafeModelError(f"{uri} is stored as {fmt}, which can run code when loaded; only {SAFE_FORMAT} is allowed")
    return mlflow.sklearn.load_model(uri)
