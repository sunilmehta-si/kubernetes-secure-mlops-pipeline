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

    def test_no_script_interpolates_event_data(self):
        # `${{ github.event.* }}` inside `run:` is pasted into the script before bash parses it: a crafted alert
        # payload or input could run commands. Event data reaches scripts only through env variables.
        for path in WORKFLOWS:
            for name, job in yaml.safe_load(path.read_text())["jobs"].items():
                for step in job.get("steps", []):
                    with self.subTest(workflow=path.name, job=name, step=step.get("name", step.get("run", ""))[:40]):
                        self.assertNotRegex(step.get("run", ""), r"\$\{\{\s*(github\.event|inputs)\.")

    def test_every_drill_script_runs_on_the_cluster_workflow(self):
        # a drill that exists but is not wired in proves nothing; the load drill was added this way
        cluster = (ROOT / ".github/workflows/cluster.yml").read_text()
        drills = sorted((ROOT / "scripts").glob("drill-*.sh"))
        self.assertGreaterEqual(len(drills), 4)
        for script in drills:
            with self.subTest(drill=script.name):
                self.assertIn(f"scripts/{script.name}", cluster)

    def test_retraining_cannot_deploy_or_promote_on_its_own(self):
        flow = yaml.safe_load((ROOT / ".github/workflows/retrain.yml").read_text())
        self.assertEqual(flow["permissions"], {"contents": "read"})
        self.assertEqual(flow[True]["repository_dispatch"]["types"], ["drift-alert"])     # YAML reads `on` as True
        scripts = "\n".join(s.get("run", "") for job in flow["jobs"].values() for s in job["steps"])
        self.assertIn("smlops retrain-check -", scripts)
        for word in ("kubectl", "cosign sign", "git push", "gh "):
            self.assertNotIn(word, scripts)


if __name__ == "__main__":
    unittest.main()
