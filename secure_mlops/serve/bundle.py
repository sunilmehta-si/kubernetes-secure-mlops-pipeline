"""A model bundle: the directory a server loads, as KServe mounts it from an OCI "modelcar" image at /mnt/models.

    <root>/model/            the MLflow model directory, exactly as stored and signed
    <root>/model.sig.json    its OpenSSF model signature
    <root>/mlbom.cdx.json    its ML-BOM (optional)
    <root>/release.json      name, version and run id

`load_verified` checks the signature against the trusted public key, refuses any format but skops, and only then
loads the model. There is no flag to skip verification: a server that cannot verify its model does not start.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from ..safeload import load as safe_load
from ..signing import SignatureError, verify

MODEL, SIGNATURE, MLBOM, RELEASE = "model", "model.sig.json", "mlbom.cdx.json", "release.json"


class BundleError(RuntimeError):
    pass


@dataclass(frozen=True)
class ServedModel:
    name: str
    version: str
    run_id: str
    digest: str                 # sha256 of the signature: names exactly the signed set of files being served
    model: object

    def predict(self, frame):
        return self.model.predict(frame)

    def predict_proba(self, frame):
        return self.model.predict_proba(frame)


def load_verified(root: str | Path, public_key: str | Path) -> ServedModel:
    root = Path(root)
    for part in (MODEL, SIGNATURE, RELEASE):
        if not (root / part).exists():
            raise BundleError(f"{root} is not a model bundle: missing {part}")
    try:
        verify(root / MODEL, root / SIGNATURE, public_key)
    except SignatureError as exc:
        raise BundleError(f"refusing to serve: {exc}") from exc
    release = json.loads((root / RELEASE).read_text())
    digest = "sha256:" + hashlib.sha256((root / SIGNATURE).read_bytes()).hexdigest()
    return ServedModel(release["name"], str(release["version"]), release.get("run_id", ""), digest,
                       safe_load(str(root / MODEL)))
