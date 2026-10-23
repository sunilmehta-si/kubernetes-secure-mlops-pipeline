import unittest
from pathlib import Path

import yaml

from secure_mlops.helm import template

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts/ticket-router"
ENVS = ROOT / "deploy/gitops/envs"
IMAGES = {"model.image": "kind-registry:5000/smlops/ticket-router-model@sha256:" + "a" * 64,
          "server.image": "kind-registry:5000/smlops-server@sha256:" + "b" * 64}


def rendered(env: str) -> dict:
    text = template(CHART, [ENVS / env / "values.yaml"], IMAGES)
    return {d["kind"]: d for d in yaml.safe_load_all(text) if d and d["kind"] != "NetworkPolicy"}


class EnvironmentTests(unittest.TestCase):
    def test_the_three_environments_exist_and_render(self):
        self.assertEqual(sorted(p.name for p in ENVS.iterdir() if p.is_dir()), ["dev", "prod", "staging"])
        for env, extra in (("dev", set()), ("staging", set()), ("prod", {"PodDisruptionBudget"})):
            with self.subTest(env=env):
                self.assertEqual(set(rendered(env)), {"InferenceService", "ServingRuntime", "ServiceAccount"} | extra)

    def test_environments_never_set_images_and_stay_small(self):
        for path in ENVS.glob("*/values.yaml"):
            with self.subTest(env=path.parent.name):
                values = yaml.safe_load(path.read_text()) or {}
                self.assertNotIn("image", values.get("model", {}))           # only a release names images
                self.assertNotIn("image", values.get("server", {}))
                self.assertLessEqual(len(path.read_text().splitlines()), 25, "an override file nobody can review")

    def test_prod_survives_losing_a_replica_and_staging_is_prod_shaped(self):
        prod = rendered("prod")["InferenceService"]["spec"]["predictor"]
        self.assertGreaterEqual(prod["minReplicas"], 2)
        defaults = template(CHART, values=IMAGES)
        self.assertEqual(template(CHART, [ENVS / "staging/values.yaml"], IMAGES), defaults)

    def test_every_environment_says_where_it_runs_under_its_own_project(self):
        for env in ("dev", "staging", "prod"):
            with self.subTest(env=env):
                target = yaml.safe_load((ENVS / env / "target.yaml").read_text())
                self.assertEqual(set(target), {"env", "server", "namespace", "project"})
                self.assertEqual((target["env"], target["project"]), (env, f"ml-serving-{env}"))
                self.assertEqual(target["namespace"], "serving")
                self.assertTrue(target["server"].startswith("https://"))

    def test_the_guide_states_each_environment_as_its_values_render_it(self):
        guide = (ROOT / "docs/environments.md").read_text()
        for env in ("dev", "staging", "prod"):
            with self.subTest(env=env):
                pred = rendered(env)["InferenceService"]["spec"]["predictor"]
                self.assertIn(f"| {env} | {pred['minReplicas']} to {pred['maxReplicas']} |", guide)
        import re
        for target in re.findall(r"\]\(([a-z.-]+\.md)", guide):
            self.assertTrue((ROOT / "docs" / target).exists(), target)


if __name__ == "__main__":
    unittest.main()
