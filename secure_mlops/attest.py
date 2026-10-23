"""Build provenance for the images the pipeline publishes (gate G5, threat T7): a SLSA v1 provenance predicate that
cosign attests to with the pipeline's key, and that admission control requires.

A signature says who published an image; provenance says what it was built from. For the model image: the source
commit, the model's name, registry version and training run, its signature file, and the datasets it was trained
and tested on by content fingerprint (from its ML-BOM). For the server image: the source commit and the dependency
lock. The build type names this repository's publish step, so the admission policy can require images that came
from it and not merely images signed by a key.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/sunilmehta-si/kubernetes-secure-mlops-pipeline"
BUILD_TYPE = f"{REPOSITORY}/publish-model@v1"
PREDICATE_TYPE = "https://slsa.dev/provenance/v1"
COMMIT = re.compile(r"^[0-9a-f]{40}$")


class ProvenanceError(ValueError):
    pass


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _builder(env: dict) -> tuple[str, str | None]:
    """(builder id, invocation id): the GitHub workflow run when in Actions, else a local build."""
    if env.get("GITHUB_ACTIONS") == "true" and env.get("GITHUB_REPOSITORY") and env.get("GITHUB_RUN_ID"):
        server = env.get("GITHUB_SERVER_URL", "https://github.com")
        workflow = env.get("GITHUB_WORKFLOW_REF", "").split("@")[0] or env["GITHUB_REPOSITORY"]
        return (f"{server}/{workflow}",
                f"{server}/{env['GITHUB_REPOSITORY']}/actions/runs/{env['GITHUB_RUN_ID']}/attempts/"
                f"{env.get('GITHUB_RUN_ATTEMPT', '1')}")
    return "local", None


def predicate(kind: str, commit: str, bundle: str | Path | None = None, env: dict | None = None,
              now: dt.datetime | None = None) -> dict:
    """The SLSA v1 provenance predicate for the `model` image (needs the bundle) or the `server` image."""
    env = dict(os.environ) if env is None else env
    if not COMMIT.fullmatch(commit):
        raise ProvenanceError(f"the source commit must be a full git SHA, got {commit!r}")
    source = {"uri": f"git+{REPOSITORY}@{commit}", "digest": {"gitCommit": commit}}
    if kind == "model":
        if bundle is None:
            raise ProvenanceError("the model image's provenance needs its bundle")
        bundle = Path(bundle)
        release = json.loads((bundle / "release.json").read_text())
        bom = json.loads((bundle / "mlbom.cdx.json").read_text())
        datasets = [{"uri": f"smlops:dataset/{c['name']}", "digest": {"sha256": c["hashes"][0]["content"]}}
                    for c in bom.get("components", []) if c.get("type") == "data"]
        if not datasets:
            raise ProvenanceError(f"{bundle}/mlbom.cdx.json names no datasets; a model's provenance must")
        external = {"kind": "model", "model": release["name"], "version": str(release["version"])}
        internal = {"trainingRun": release.get("run_id", ""),
                    "modelSignatureSha256": _sha256(bundle / "model.sig.json")}
        resolved = [source, *datasets]
    elif kind == "server":
        external = {"kind": "server"}
        internal = {}
        resolved = [source, {"uri": "smlops:requirements-lock.txt",
                             "digest": {"sha256": _sha256(ROOT / "requirements-lock.txt")}}]
    else:
        raise ProvenanceError(f"kind must be model or server, got {kind!r}")
    builder, invocation = _builder(env)
    metadata = {"startedOn": (now or dt.datetime.now(dt.timezone.utc)).isoformat(timespec="seconds")}
    if invocation:
        metadata["invocationId"] = invocation
    return {"buildDefinition": {"buildType": BUILD_TYPE, "externalParameters": external,
                                "internalParameters": internal, "resolvedDependencies": resolved},
            "runDetails": {"builder": {"id": builder}, "metadata": metadata}}
