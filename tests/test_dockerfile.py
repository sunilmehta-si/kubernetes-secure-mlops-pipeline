import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEXT = (ROOT / "Dockerfile").read_text()


class DockerfileTests(unittest.TestCase):
    def test_base_is_pinned_by_digest(self):
        base = re.search(r"^FROM (\S+)", TEXT, re.M).group(1)
        self.assertRegex(base, r"@sha256:[0-9a-f]{64}$")

    def test_runs_as_a_non_root_user(self):
        users = re.findall(r"^USER (\S+)", TEXT, re.M)
        self.assertTrue(users and not users[-1].startswith(("0", "root")), users)

    def test_secrets_and_local_state_stay_out_of_the_build_context(self):
        ignored = (ROOT / ".dockerignore").read_text().split()
        for path in (".git", "mlflow.db", "data/", "runs/"):
            self.assertIn(path, ignored)
