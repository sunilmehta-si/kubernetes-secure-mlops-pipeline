import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github/workflows").glob("*.yml"))


class WorkflowTests(unittest.TestCase):
    def test_every_step_fails_when_any_command_in_a_pipe_fails(self):
        # GitHub's default shell is `bash -e {0}`: `drill.sh | tee out.txt` succeeds even when drill.sh fails.
        # This hid failing drills on the cluster run until it was caught; `shell: bash` adds -o pipefail.
        self.assertTrue(WORKFLOWS)
        for path in WORKFLOWS:
            with self.subTest(workflow=path.name):
                flow = yaml.safe_load(path.read_text())
                self.assertEqual(flow.get("defaults", {}).get("run", {}).get("shell"), "bash")
                for job in flow["jobs"].values():
                    for step in job.get("steps", []):
                        self.assertIn(step.get("shell", "bash"), {"bash"}, step.get("name", step.get("run")))

    def test_workflows_start_with_read_only_permissions(self):
        for path in WORKFLOWS:
            with self.subTest(workflow=path.name):
                self.assertEqual(yaml.safe_load(path.read_text())["permissions"], {"contents": "read"})


if __name__ == "__main__":
    unittest.main()
