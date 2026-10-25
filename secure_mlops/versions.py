"""Compare deploy/versions.env with the releases upstream has published.

Every platform component is pinned to an exact release in deploy/versions.env, so nothing moves on its own. The
price is that nothing moves at all unless someone looks: a patch release that closes a CVE sits unused until a
person notices. `smlops versions` is that look, run on every CI run and printed into the job summary: each pin,
the latest release upstream, and how far behind it is. It reports; the decision to bump stays with a person,
because a bump is a change to the cluster that the cluster workflow has to prove.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ROOT / "deploy" / "versions.env"
# Which GitHub repository publishes each pinned component. Pins without an entry (the registry, the kind context,
# checksums, the node image) are not releases and are not compared.
COMPONENTS = {
    "KIND_VERSION": "kubernetes-sigs/kind",
    "CERT_MANAGER_VERSION": "cert-manager/cert-manager",
    "KSERVE_VERSION": "kserve/kserve",
    "KYVERNO_VERSION": "kyverno/kyverno",
    "ZOT_VERSION": "project-zot/zot",
    "COSIGN_VERSION": "sigstore/cosign",
    "PROMETHEUS_VERSION": "prometheus/prometheus",
    "ALERTMANAGER_VERSION": "prometheus/alertmanager",
    "ARGOCD_VERSION": "argoproj/argo-cd",
    "ORAS_VERSION": "oras-project/oras",
    "HELM_VERSION": "helm/helm",
    "UV_VERSION": "astral-sh/uv",
    "OSV_SCANNER_VERSION": "google/osv-scanner",
}
SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")

Fetch = Callable[[str], str]


class VersionsError(RuntimeError):
    pass


@dataclass(frozen=True)
class Row:
    component: str
    repo: str
    pinned: str
    latest: str
    # "current", or the largest part that moved: "major", "minor" or "patch"
    status: str

    @property
    def behind(self) -> bool:
        return self.status != "current"


def load_pins(path: Path = VERSIONS) -> dict[str, str]:
    pins = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            pins[key.strip()] = value.strip()
    return pins


def github_latest(repo: str) -> str:
    """The tag of the repository's latest (non-prerelease) release, through the API; GH_TOKEN or GITHUB_TOKEN raises
    the anonymous rate limit when set."""
    request = urllib.request.Request(f"https://api.github.com/repos/{repo}/releases/latest",
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": "smlops-versions"})
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return str(json.load(response)["tag_name"])
    except (OSError, ValueError, KeyError) as exc:
        raise VersionsError(f"{repo}: could not read the latest release: {exc}") from exc


def compare(pinned: str, latest: str) -> str:
    a, b = SEMVER.match(pinned), SEMVER.match(latest)
    if not a or not b:
        raise VersionsError(f"not release versions: {pinned!r}, {latest!r}")
    pin, new = tuple(int(x) for x in a.groups()), tuple(int(x) for x in b.groups())
    if new <= pin:
        return "current"
    return ("major", "minor", "patch")[next(i for i in range(3) if new[i] != pin[i])]


def report(pins: dict[str, str], fetch: Fetch | None = None) -> list[Row]:
    # Resolved at call time, not at import: a test that replaces github_latest on the module must not reach the network.
    fetch = fetch or github_latest
    rows = []
    for component, repo in COMPONENTS.items():
        if component not in pins:
            continue
        latest = fetch(repo)
        rows.append(Row(component, repo, pins[component], latest, compare(pins[component], latest)))
    return rows


def markdown(rows: list[Row]) -> str:
    behind = [r for r in rows if r.behind]
    lines = [f"## Pinned versions: {len(behind)} of {len(rows)} behind upstream" if behind else
             f"## Pinned versions: all {len(rows)} current", "",
             "| Component | Pinned | Latest release | Status |", "|---|---|---|---|"]
    for r in rows:
        status = "current" if not r.behind else f"**{r.status} behind**"
        lines.append(f"| {r.component} ([{r.repo}](https://github.com/{r.repo}/releases)) | {r.pinned} | {r.latest} | {status} |")
    return "\n".join(lines) + "\n"
