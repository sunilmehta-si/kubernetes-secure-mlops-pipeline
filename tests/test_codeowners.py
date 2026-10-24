import fnmatch
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULES = [line.split() for line in (ROOT / ".github/CODEOWNERS").read_text().splitlines()
         if line.strip() and not line.startswith("#")]


def owners(path: str) -> list[str]:
    """The last matching rule wins, as on GitHub."""
    found: list[str] = []
    for pattern, *who in RULES:
        p = pattern.lstrip("/")
        if pattern == "*" or path == p or (p.endswith("/") and path.startswith(p)) or fnmatch.fnmatch(path, p):
            found = who
    return found


class CodeownersTests(unittest.TestCase):
    def test_release_decisions_and_the_build_have_owners(self):
        for path in ("configs/gate.yaml", "configs/train.yaml", "secure_mlops/gate.py", "secure_mlops/validate.py",
                     "secure_mlops/registry.py", "secure_mlops/safeload.py", ".github/workflows/ci.yml",
                     "Dockerfile", "pyproject.toml", "secure_mlops/data.py",
                     "deploy/gitops/rendered/prod/inferenceservice.yaml", "deploy/policy/verify-signatures.yaml",
                     "deploy/monitoring/alerts.yaml", "secure_mlops/gitops.py", "secure_mlops/serve/auth.py",
                     "secure_mlops/relay.py", "deploy/monitoring/alertmanager.yml", "uv.lock",
                     "configs/models/ticket-router.yaml", "scripts/install-argocd.sh",
                     "charts/ticket-router/values.yaml", "deploy/gitops/envs/prod/values.yaml"):
            with self.subTest(path=path):
                self.assertTrue(owners(path), f"{path} has no code owner")

    def test_every_named_path_exists(self):
        for pattern, *_ in RULES:
            if pattern != "*":
                self.assertTrue((ROOT / pattern.lstrip("/")).exists(), pattern)
