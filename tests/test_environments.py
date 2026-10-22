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
        for env in ("dev", "staging", "prod"):
            with self.subTest(env=env):
                self.assertEqual(set(rendered(env)), {"InferenceService", "ServingRuntime", "ServiceAccount"})

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


if __name__ == "__main__":
    unittest.main()
