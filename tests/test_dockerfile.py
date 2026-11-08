import re
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

    def test_the_image_has_no_package_manager(self):
        self.assertRegex(TEXT, r"dpkg --purge [^\n]*\bapt\b[^\n]*\blibapt-pkg")
        installs = re.findall(r"apt-get install[^\n\\]*", TEXT)
        self.assertTrue(installs and all("--only-upgrade" in line for line in installs),
                        "apt may only upgrade what the base image already has, never add a package")
        self.assertLess(TEXT.index("apt-get install"), TEXT.index("dpkg --purge"))   # the upgrade comes before apt goes

    def test_secrets_and_local_state_stay_out_of_the_build_context(self):
        ignored = (ROOT / ".dockerignore").read_text().split()
        for path in (".git", "mlflow.db", "data/", "runs/"):
            self.assertIn(path, ignored)
