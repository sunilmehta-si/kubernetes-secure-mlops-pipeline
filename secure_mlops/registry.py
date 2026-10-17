"""Model registry: every trained model is registered; only a gate pass moves the champion alias.

Aliases carry the release state. `challenger` is the newest registered model, `champion` is what production should
serve, and `previous` is the champion it replaced, kept for one-step rollback. The champion is loaded and evaluated
on the current test set when a challenger is judged, so both are always compared on the same tickets.
"""
from __future__ import annotations

from dataclasses import dataclass

import mlflow
from mlflow import MlflowClient
from mlflow.exceptions import MlflowException

from .safeload import load as safe_load

CHAMPION, CHALLENGER, PREVIOUS = "champion", "challenger", "previous"


@dataclass(frozen=True)
class Version:
    name: str
    version: str

    @property
    def uri(self) -> str:
        return f"models:/{self.name}/{self.version}"


def register(model_uri: str, name: str, tags: dict[str, str]) -> Version:
    mv = mlflow.register_model(model_uri, name, tags=tags)
    MlflowClient().set_registered_model_alias(name, CHALLENGER, mv.version)
    return Version(name, str(mv.version))


def by_alias(name: str, alias: str) -> Version | None:
    try:
        mv = MlflowClient().get_model_version_by_alias(name, alias)
    except MlflowException:
        return None                     # no such model or alias yet
    return Version(name, str(mv.version))


def load(version: Version):
    """Load a registered version; anything not stored in a safe format is refused before it is opened."""
    return safe_load(version.uri)


def promote(version: Version, decision: dict) -> bool:
    """Make `version` the champion if the gate passed; the old champion becomes `previous`."""
    client = MlflowClient()
    client.set_model_version_tag(version.name, version.version, "gate", "passed" if decision["passed"] else "failed")
    if not decision["passed"]:
        return False
    current = by_alias(version.name, CHAMPION)
    if current is not None and current.version != version.version:
        client.set_registered_model_alias(version.name, PREVIOUS, current.version)
    client.set_registered_model_alias(version.name, CHAMPION, version.version)
    return True


def rollback(name: str) -> Version:
    """Point champion back at previous; for when production shows what the gate could not."""
    previous = by_alias(name, PREVIOUS)
    if previous is None:
        raise ValueError(f"{name} has no previous champion to roll back to")
    MlflowClient().set_registered_model_alias(name, CHAMPION, previous.version)
    MlflowClient().delete_registered_model_alias(name, PREVIOUS)
    return previous
