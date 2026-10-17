import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from secure_mlops.provenance import git_info


def sh(cwd, *args):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


class GitInfoTests(unittest.TestCase):
    def test_commit_and_dirty_flag_from_a_checkout(self):
        with tempfile.TemporaryDirectory() as d:
            sh(d, "git", "init", "-q")
            sh(d, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "x")
            Path(d, "f.txt").write_text("1")
            sh(d, "git", "add", "f.txt")
            sh(d, "git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "y")
            clean = git_info(d)
            self.assertRegex(clean["code.git_commit"], r"^[0-9a-f]{40}$")
            self.assertEqual((clean["code.git_dirty"], clean["code.source"]), ("false", "git"))
            Path(d, "f.txt").write_text("2")                  # an uncommitted change to a tracked file
            self.assertEqual(git_info(d)["code.git_dirty"], "true")

    def test_falls_back_to_the_environment_outside_a_checkout(self):
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict(os.environ, {"GITHUB_SHA": "a" * 40}, clear=False):
                os.environ.pop("SMLOPS_GIT_COMMIT", None)
                info = git_info(d)
            self.assertEqual((info["code.git_commit"], info["code.source"]), ("a" * 40, "environment"))
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(git_info(d)["code.git_commit"], "unknown")


class EnvironmentTests(unittest.TestCase):
    def test_frozen_requirements_and_hash_identify_the_environment(self):
        from secure_mlops.provenance import environment_info, frozen_requirements
        deps = frozen_requirements()
        self.assertIn("scikit-learn==", deps)
        self.assertEqual(deps, frozen_requirements())                  # stable order
        info = environment_info()
        self.assertTrue(info["env.deps_hash"].startswith("sha256:"))
        self.assertEqual(info["env.image"], os.environ.get("SMLOPS_IMAGE", "local"))
        with mock.patch.dict(os.environ, {"SMLOPS_IMAGE": "ghcr.io/x/y@sha256:" + "b" * 64}):
            self.assertTrue(environment_info()["env.image"].endswith("b" * 64))
