"""Verify a published image the way admission will, before anything is rendered or committed (gate G5, threat T7).

Kyverno checks signatures and attestations when a pod is created; this checks the same things earlier, with
cosign, so a release that would be rejected on the cluster never reaches a pull request:

- the image is signed with the pipeline's cosign key;
- it carries a SLSA v1 provenance attestation from this repository's publish step, naming the expected model
  and version for a model image;
- a model image also carries its CycloneDX ML-BOM attestation, describing a machine-learning model.

The key was made for the pipeline and never uploaded to a transparency log, so the log is ignored on purpose.
"""
from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
from pathlib import Path

from .attest import BUILD_TYPE, PREDICATE_TYPE

BOM_TYPE = "https://cyclonedx.org/bom"
DIGEST_REF = re.compile(r"^[a-z0-9.-]+(:[0-9]+)?(/[a-z0-9._-]+)+@sha256:[0-9a-f]{64}$")


class ImageError(ValueError):
    pass


def _cosign(args: list[str], cosign: str) -> str:
    binary = shutil.which(cosign) or cosign
    if not Path(binary).exists() and shutil.which(cosign) is None:
        raise ImageError(f"cosign is needed to verify images; {cosign} was not found")
    run = subprocess.run([binary, *args], capture_output=True, text=True, check=False)
    if run.returncode != 0:
        detail = (run.stderr or run.stdout).strip().splitlines()
        raise ImageError(detail[-1] if detail else f"cosign {args[0]} failed")
    return run.stdout


def _flags(public_key: str | Path, plain_http: bool) -> list[str]:
    flags = ["--key", str(public_key), "--insecure-ignore-tlog=true"]
    if plain_http:
        flags.append("--allow-http-registry")
    return flags


def _predicates(output: str, predicate_type: str) -> list[dict]:
    """The predicates of the given type in cosign's verify-attestation output (one DSSE envelope per line)."""
    found = []
    for line in output.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        envelope = json.loads(line)
        statement = json.loads(base64.b64decode(envelope["payload"]))
        if statement.get("predicateType") == predicate_type:
            found.append(statement.get("predicate") or {})
    return found


def verify_image(ref: str, public_key: str | Path, kind: str, model: str | None = None,
                 version: str | None = None, plain_http: bool = False, cosign: str = "cosign") -> dict:
    """Verify one image; returns what was checked. kind is "model" or "server"; a model image must name the
    expected model and version in its provenance."""
    if kind not in ("model", "server"):
        raise ImageError(f"kind must be model or server, got {kind!r}")
    if not DIGEST_REF.fullmatch(ref):
        raise ImageError(f"verify images by digest (registry/repository@sha256:...), got {ref!r}")
    flags = _flags(public_key, plain_http)
    _cosign(["verify", *flags, ref], cosign)
    provenance = _predicates(_cosign(["verify-attestation", "--type", "slsaprovenance1", *flags, ref], cosign),
                             PREDICATE_TYPE)
    if not provenance:
        raise ImageError(f"{ref} has no SLSA provenance attestation signed with this key")
    ours = [p for p in provenance if p.get("buildDefinition", {}).get("buildType") == BUILD_TYPE]
    if not ours:
        raise ImageError(f"{ref}: provenance was not produced by this repository's publish step")
    params = ours[0]["buildDefinition"].get("externalParameters", {})
    if params.get("kind") != kind:
        raise ImageError(f"{ref}: provenance says this is a {params.get('kind')!r} image, expected {kind}")
    checked = {"ref": ref, "kind": kind, "signature": True, "provenance": True,
               "source": next((d.get("digest", {}).get("gitCommit") for d in
                               ours[0]["buildDefinition"].get("resolvedDependencies", [])
                               if d.get("digest", {}).get("gitCommit")), None)}
    if kind == "model":
        if (model and params.get("model") != model) or (version and str(params.get("version")) != str(version)):
            raise ImageError(f"{ref}: provenance is for {params.get('model')} v{params.get('version')}, "
                             f"expected {model} v{version}")
        boms = _predicates(_cosign(["verify-attestation", "--type", "cyclonedx", *flags, ref], cosign), BOM_TYPE)
        if not any(b.get("metadata", {}).get("component", {}).get("type") == "machine-learning-model" for b in boms):
            raise ImageError(f"{ref} has no ML-BOM attestation describing a machine-learning model")
        checked.update({"ml_bom": True, "model": params.get("model"), "version": str(params.get("version"))})
    return checked
