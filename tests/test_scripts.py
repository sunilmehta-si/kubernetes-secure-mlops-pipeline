import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ScriptTests(unittest.TestCase):
    def test_scripts_are_valid_bash_strict_and_executable(self):
        scripts = sorted((ROOT / "scripts").glob("*.sh"))
        self.assertTrue(scripts)
        for script in scripts:
            with self.subTest(script=script.name):
                self.assertEqual(subprocess.run(["bash", "-n", str(script)], capture_output=True).returncode, 0)
                self.assertTrue(script.stat().st_mode & 0o111, "not executable")
                self.assertIn("set -euo pipefail", script.read_text())
                self.assertIn(". deploy/versions.env", script.read_text())       # one source of versions

    def test_cluster_scripts_act_only_on_the_kind_cluster(self):
        for script in sorted((ROOT / "scripts").glob("*.sh")):
            if script.name == "cluster-up.sh":                                  # it creates and selects the context
                continue
            code = [line for line in script.read_text().splitlines() if not line.lstrip().startswith("#")]
            calls = [i for i, line in enumerate(code) if "kubectl " in line]
            if not calls:
                continue
            with self.subTest(script=script.name):
                guard = next((i for i, line in enumerate(code) if '= "$KIND_CONTEXT" ]' in line), None)
                self.assertIsNotNone(guard, "no kubectl context guard")
                self.assertEqual(calls[0], guard, "kubectl runs before the context guard")

    def test_guard_refuses_any_other_context(self):
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d, "kubectl")
            fake.write_text("#!/bin/sh\n[ \"$1 $2\" = \"config current-context\" ] && echo gke_prod && exit 0\n"
                            "echo \"$@\" >> \"$(dirname \"$0\")/calls\"\n")
            fake.chmod(0o755)
            env = {**os.environ, "PATH": f"{d}:{os.environ['PATH']}"}
            run = subprocess.run(["bash", str(ROOT / "scripts/install-monitoring.sh")], env=env, capture_output=True,
                                 text=True)
            self.assertEqual(run.returncode, 1)
            self.assertIn("kubectl context is not kind-smlops", run.stderr)
            self.assertFalse(Path(d, "calls").exists(), "a kubectl command ran against the wrong cluster")
