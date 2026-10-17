"""Where a run's code and environment came from, recorded on every run so a model can be rebuilt exactly.

The git commit alone is not enough: a run from a working tree with uncommitted changes cannot be reproduced from the
commit, so it is flagged as dirty. Inside a pipeline container there is no git checkout; the commit then comes from
the environment (GITHUB_SHA in CI, SMLOPS_GIT_COMMIT when an image is built).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

UNKNOWN = "unknown"


def _git(root: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip()


def git_info(root: str | Path | None = None) -> dict[str, str]:
    root = Path(root) if root else Path(__file__).resolve().parents[1]
    commit = _git(root, "rev-parse", "HEAD")
    if commit:
        dirty = bool(_git(root, "status", "--porcelain", "--untracked-files=no"))
        return {"code.git_commit": commit, "code.git_dirty": str(dirty).lower(), "code.source": "git"}
    env = os.environ.get("SMLOPS_GIT_COMMIT") or os.environ.get("GITHUB_SHA")
    return {"code.git_commit": env or UNKNOWN, "code.git_dirty": UNKNOWN, "code.source": "environment" if env else UNKNOWN}
