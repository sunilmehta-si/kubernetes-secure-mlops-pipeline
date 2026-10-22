"""Run the pinned Helm to render a chart (gate G2: the same inputs give the same manifests).

Only the Helm version in deploy/versions.env is used: another version can render the same chart differently, and
a release must not depend on whichever Helm a machine happens to have. The binary is $SMLOPS_HELM, or `helm` on
PATH. Rendering is `helm template` only: nothing here talks to a cluster.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class HelmError(RuntimeError):
    pass


def pinned_version() -> str:
    for line in (ROOT / "deploy" / "versions.env").read_text().splitlines():
        if line.startswith("HELM_VERSION="):
            return line.split("=", 1)[1].strip()
    raise HelmError("deploy/versions.env names no HELM_VERSION")


def binary() -> str:
    helm = os.environ.get("SMLOPS_HELM") or shutil.which("helm")
    if not helm:
        raise HelmError(f"Helm {pinned_version()} is needed to render releases: install it or set SMLOPS_HELM")
    version = subprocess.run([helm, "version", "--short"], capture_output=True, text=True, check=False).stdout
    if not version.startswith(pinned_version() + "+") and version.strip() != pinned_version():
        raise HelmError(f"{helm} is Helm {version.strip() or 'unknown'}; releases are rendered with "
                        f"{pinned_version()} (deploy/versions.env)")
    return helm


def template(chart: str | Path, values_files: list[str | Path] = (), values: dict[str, str] | None = None,
             release: str = "ticket-router") -> str:
    """`helm template` with values files applied in order, then --set-string values; the rendered YAML."""
    args = [binary(), "template", release, str(chart)]
    for f in values_files:
        args += ["--values", str(f)]
    for key, value in (values or {}).items():
        args += ["--set-string", f"{key}={value}"]
    run = subprocess.run(args, capture_output=True, text=True, check=False)
    if run.returncode != 0:
        lines = [line.strip() for line in run.stderr.splitlines()
                 if line.strip() and not line.startswith("Use --debug")]
        raise HelmError(" ".join(lines).removeprefix("Error: ") or "helm template failed")
    return run.stdout
