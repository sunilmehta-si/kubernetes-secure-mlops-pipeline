"""Model registry: every trained model is registered; only a gate pass moves the champion alias.

Aliases carry the release state. `challenger` is the newest registered model, `champion` is what production should
serve, and `previous` is the champion it replaced, kept for one-step rollback. The champion is loaded and evaluated
on the current test set when a challenger is judged, so both are always compared on the same tickets.

Retiring a model (gate G7) removes all three aliases, so nothing can be packaged or promoted from it, and records
when, why and by whom on the registered model. Its versions stay, with their signatures and records, for audit.
"""
from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from pathlib import Path

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


SIGNATURE_ARTIFACT = "model.sig.json"     # stored on the training run, next to the model it signs


def sign_stored_model(model_uri: str, run_id: str, private_key: str | Path) -> str:
    """Sign the model's files exactly as the store holds them, and attach the signature to the run."""
    import tempfile

    from .signing import sign
    with tempfile.TemporaryDirectory() as d:
        local = mlflow.artifacts.download_artifacts(artifact_uri=model_uri, dst_path=f"{d}/model")
        sig = sign(local, Path(d) / SIGNATURE_ARTIFACT, private_key)
        MlflowClient().log_artifact(run_id, str(sig))
    return f"runs:/{run_id}/{SIGNATURE_ARTIFACT}"


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


def verify_key(explicit: str | Path | None = None) -> Path | None:
    """The public key models must be signed with: an explicit path, or SMLOPS_VERIFY_KEY."""
    value = explicit or os.environ.get("SMLOPS_VERIFY_KEY")
    return Path(value) if value else None


def load(version: Version, public_key: str | Path | None = None):
    """Load a registered version. With a verification key, the stored files must match the version's signature
    first (an unsigned version is refused); in every case only the safe format is opened."""
    key = verify_key(public_key)
    if key is None:
        return safe_load(version.uri)
    import tempfile

    from .signing import SignatureError, verify
    mv = MlflowClient().get_model_version(version.name, version.version)
    signature = mv.tags.get("signature", "unsigned")
    if signature == "unsigned":
        raise SignatureError(f"{version.name} v{version.version} is unsigned; refusing to load it")
    with tempfile.TemporaryDirectory() as d:
        local = mlflow.artifacts.download_artifacts(artifact_uri=version.uri, dst_path=f"{d}/model")
        sig = mlflow.artifacts.download_artifacts(artifact_uri=signature, dst_path=f"{d}/sig")
        verify(local, sig, key)
        return safe_load(local)                       # load the verified copy, not a second download


def retired(name: str) -> dict | None:
    """{at, reason, by} if the model was retired, else None."""
    try:
        tags = MlflowClient().get_registered_model(name).tags
    except MlflowException:
        return None
    if tags.get("retired") != "true":
        return None
    return {"at": tags.get("retired_at", ""), "reason": tags.get("retired_reason", ""), "by": tags.get("retired_by", "")}


def retire(name: str, reason: str, by: str) -> list[str]:
    """Retire a registered model: remove its release aliases and record why. Returns the aliases removed."""
    if not reason.strip() or not by.strip():
        raise ValueError("retiring a model needs a reason and who decided it")
    client = MlflowClient()
    client.get_registered_model(name)                 # raises if there is no such model
    removed = []
    for alias in (CHAMPION, CHALLENGER, PREVIOUS):
        if by_alias(name, alias) is not None:
            client.delete_registered_model_alias(name, alias)
            removed.append(alias)
    for key, value in {"retired": "true", "retired_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                       "retired_reason": reason.strip(), "retired_by": by.strip()}.items():
        client.set_registered_model_tag(name, key, value)
    return removed


def promote(version: Version, decision: dict) -> bool:
    """Make `version` the champion if the gate passed; the old champion becomes `previous`. A retired model is
    never promoted, whatever the gate says."""
    client = MlflowClient()
    if retired(version.name):
        raise ValueError(f"{version.name} is retired; nothing is promoted for it")
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
