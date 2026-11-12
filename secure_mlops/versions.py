"""Compare deploy/versions.env with the releases upstream has published.

Every platform component is pinned to an exact release in deploy/versions.env, so nothing moves on its own. The
price is that nothing moves at all unless someone looks: a patch release that closes a CVE sits unused until a
person notices. `smlops versions` is that look, run on every CI run and printed into the job summary: each pin,
the latest release upstream, and how far behind it is. It reports; the decision to bump stays with a person,
because a bump is a change to the cluster that the cluster workflow has to prove.

A release is adopted only once it is MIN_RELEASE_AGE_DAYS old. A compromised or broken release is usually found and
withdrawn within days of publication, so a pin that waits two weeks does not take it; the report says when each
newer release becomes adoptable, and `--strict` fails only on the ones that already are. When the newest release is
too new, an earlier one past the pin may already be old enough (uv 0.13.6 while 0.13.7 waits), and the report names
it, so a fix that has aged is not held back by the one released after it.
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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
# Pins that are not releases to compare, and why. Every line in deploy/versions.env is in COMPONENTS or here, so a
# new pin has to say which it is.
NOT_COMPARED = {
    "KIND_NODE_IMAGE": "pinned by digest and chosen from the kind release's notes, which list the node images built for it",
    "KIND_CONTEXT": "the kubectl context the scripts refuse to act outside of",
    "REGISTRY": "the in-cluster registry address the scripts push to",
    "KYVERNO_CLI_SHA256_LINUX_X86_64": "the checksum of the pinned download",
    "PROMETHEUS_SHA256_LINUX_AMD64": "the checksum of the pinned download",
    "ALERTMANAGER_SHA256_LINUX_AMD64": "the checksum of the pinned download",
    "ORAS_SHA256_LINUX_AMD64": "the checksum of the pinned download",
    "HELM_SHA256_LINUX_AMD64": "the checksum of the pinned download",
    "UV_SHA256_LINUX_X86_64": "the checksum of the pinned download",
    "OSV_SCANNER_SHA256_LINUX_AMD64": "the checksum of the pinned download",
}
SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
MIN_RELEASE_AGE_DAYS = 14


@dataclass(frozen=True)
class Release:
    tag: str
    published: date | None = None


# Returns the latest release, or just its tag when the publication date is not known.
Fetch = Callable[[str], "Release | str"]


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
    published: date | None = None
    checked_on: date | None = None
    # The newest release past the pin that is already old enough, when the latest is not.
    ready: Release | None = None

    @property
    def behind(self) -> bool:
        return self.status != "current"

    @property
    def adoptable_on(self) -> date | None:
        return self.published + timedelta(days=MIN_RELEASE_AGE_DAYS) if self.published else None

    @property
    def too_new(self) -> bool:
        """Behind, but the newer release is younger than MIN_RELEASE_AGE_DAYS."""
        return self.behind and self.adoptable_on is not None and (self.checked_on or date.today()) < self.adoptable_on

    @property
    def adopt_now(self) -> bool:
        """A release past the pin can be taken today: the latest, or an earlier one when the latest is too new."""
        return self.behind and (not self.too_new or self.ready is not None)


def load_pins(path: Path = VERSIONS) -> dict[str, str]:
    pins = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            pins[key.strip()] = value.strip()
    return pins


def _github_json(path: str) -> object:
    """One GitHub API GET; GH_TOKEN or GITHUB_TOKEN raises the rate limit when set."""
    request = urllib.request.Request(f"https://api.github.com/{path}",
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": "smlops-versions"})
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=20) as response:   # noqa: S310 - fixed https API URL
        return json.load(response)


def _release(data: dict) -> Release:
    published = data.get("published_at")
    return Release(str(data["tag_name"]), datetime.fromisoformat(published.replace("Z", "+00:00")).date() if published else None)


def _semver(tag: str) -> tuple[int, int, int]:
    return tuple(int(x) for x in SEMVER.match(tag).groups())


def _stable_listing(repo: str) -> list[dict]:
    """The newest 50 releases that are published, not drafts or prereleases, and plain x.y.z."""
    listing = _github_json(f"repos/{repo}/releases?per_page=50")
    if not isinstance(listing, list):
        raise ValueError("expected a list of releases")
    return [r for r in listing if not r.get("draft") and not r.get("prerelease") and SEMVER.match(str(r.get("tag_name")))]


def github_stable(repo: str) -> list[Release]:
    """Every stable release among the newest 50, highest version first."""
    try:
        return [_release(r) for r in sorted(_stable_listing(repo), key=lambda r: _semver(r["tag_name"]), reverse=True)]
    except (OSError, ValueError, KeyError) as exc:
        raise VersionsError(f"{repo}: could not read the releases: {exc}") from exc


def newest_ready(pinned: str, releases: list[Release], today: date) -> Release | None:
    """The highest release past the pin that is MIN_RELEASE_AGE_DAYS old by today, if any."""
    if not SEMVER.match(pinned):
        return None
    old_enough = [r for r in releases if SEMVER.match(r.tag) and _semver(r.tag) > _semver(pinned) and r.published
                  and r.published + timedelta(days=MIN_RELEASE_AGE_DAYS) <= today]
    return max(old_enough, key=lambda r: _semver(r.tag), default=None)


def github_latest(repo: str) -> Release:
    """The newest stable release: the highest plain x.y.z among published, non-draft, non-prerelease releases.

    GitHub's `releases/latest` returns whatever the maintainers mark latest, and some projects mark their release
    candidates (vLLM's v0.33.0rc3), which neither compares with a pin nor is anything to adopt. The same call that
    lists the releases shows the stable ones. A repository with no plain x.y.z release falls back to
    `releases/latest`."""
    try:
        stable = _stable_listing(repo)
        if stable:
            return _release(max(stable, key=lambda r: _semver(r["tag_name"])))
        return _release(_github_json(f"repos/{repo}/releases/latest"))
    except (OSError, ValueError, KeyError) as exc:
        raise VersionsError(f"{repo}: could not read the latest release: {exc}") from exc


def github_release_body(repo: str, tag: str) -> str:
    """The release notes of one tag through the API."""
    try:
        return str(_github_json(f"repos/{repo}/releases/tags/{tag}").get("body") or "")
    except (OSError, ValueError) as exc:
        raise VersionsError(f"{repo} {tag}: could not read the release notes: {exc}") from exc


def node_image_listed(pins: dict[str, str], fetch_body: Callable[[str, str], str] | None = None) -> bool:
    """True when the pinned node image, digest included, is one the pinned kind release's notes list.

    kind builds node images per release and says to use them by digest; an image from another release can start
    and fail in ways that look like the platform under test. The notes list every image as
    `kindest/node:<k8s>@sha256:<digest>`, so the check is that exact string."""
    fetch_body = fetch_body or github_release_body
    return pins["KIND_NODE_IMAGE"] in fetch_body(COMPONENTS["KIND_VERSION"], pins["KIND_VERSION"])


def compare(pinned: str, latest: str) -> str:
    a, b = SEMVER.match(pinned), SEMVER.match(latest)
    if not a or not b:
        raise VersionsError(f"not release versions: {pinned!r}, {latest!r}")
    pin, new = tuple(int(x) for x in a.groups()), tuple(int(x) for x in b.groups())
    if new <= pin:
        return "current"
    return ("major", "minor", "patch")[next(i for i in range(3) if new[i] != pin[i])]


def report(pins: dict[str, str], fetch: Fetch | None = None, today: date | None = None,
           fetch_all: Callable[[str], list[Release]] | None = None) -> list[Row]:
    """One row per pin. Only when the latest release is too new are the earlier ones listed, to find one that is old
    enough: one more API call, for the few pins that need it. A caller that brings its own fetch and no fetch_all
    gets no such lookup."""
    # Resolved at call time, not at import: a test that replaces github_latest on the module must not reach the network.
    if fetch_all is None and fetch is None:
        fetch_all = github_stable
    fetch = fetch or github_latest
    today = today or date.today()
    rows = []
    for component, repo in COMPONENTS.items():
        if component not in pins:
            continue
        latest = fetch(repo)
        release = latest if isinstance(latest, Release) else Release(str(latest))
        row = Row(component, repo, pins[component], release.tag, compare(pins[component], release.tag),
                  release.published, today)
        if row.too_new and fetch_all is not None:
            row = Row(row.component, repo, row.pinned, row.latest, row.status, row.published, today,
                      newest_ready(row.pinned, fetch_all(repo), today))
        rows.append(row)
    return rows


def markdown(rows: list[Row], node_image_ok: bool | None = None) -> str:
    behind = [r for r in rows if r.behind]
    waiting = [r for r in behind if r.too_new and r.ready is None]
    title = (f"## Pinned versions: {len(behind)} of {len(rows)} behind upstream" if behind else
             f"## Pinned versions: all {len(rows)} current")
    if waiting:
        title += f", {len(waiting)} of them too new to adopt yet"
    lines = [title, "", "| Component | Pinned | Latest release | Status |", "|---|---|---|---|"]
    for r in rows:
        if not r.behind:
            status = "current"
        elif r.too_new:
            status = f"{r.status} behind; published {r.published}, adoptable from {r.adoptable_on}"
            if r.ready:
                status += f"; **{r.ready.tag}** (published {r.ready.published}) is adoptable now"
        else:
            status = f"**{r.status} behind**"
        lines.append(f"| {r.component} ([{r.repo}](https://github.com/{r.repo}/releases)) | {r.pinned} | {r.latest} | {status} |")
    if any(r.too_new for r in rows):
        lines += ["", f"Releases are adopted once they are {MIN_RELEASE_AGE_DAYS} days old, so a compromised or withdrawn "
                      "release has time to come to light first."]
    lines += ["", "Not compared: " + "; ".join(f"{name} ({why})" for name, why in NOT_COMPARED.items()) + "."]
    if node_image_ok is not None:
        lines += ["", "Node image: " + ("listed, digest and all, in the pinned kind release's notes." if node_image_ok else
                  "**not among the images the pinned kind release's notes list**: it was built for another kind release.")]
    lines.append("")
    return "\n".join(lines) + "\n"


def as_json(rows: list[Row], node_image_ok: bool | None = None) -> list[dict]:
    """The report as data: one object per pin, with dates as ISO strings and nothing derived left implicit; the node
    image check rides along as a final object when it ran, so a tool reads one file for everything the report knows."""
    out = [{"component": r.component, "repository": r.repo, "pinned": r.pinned, "latest": r.latest, "status": r.status,
            "published": r.published.isoformat() if r.published else None,
            "adoptable_on": r.adoptable_on.isoformat() if r.adoptable_on else None,
            "too_new": r.too_new, "adopt_now": r.adopt_now,
            "ready": r.ready.tag if r.ready else None,
            "ready_published": r.ready.published.isoformat() if r.ready and r.ready.published else None} for r in rows]
    if node_image_ok is not None:
        out.append({"component": "KIND_NODE_IMAGE", "check": "listed in the pinned kind release's notes", "ok": node_image_ok})
    return out

