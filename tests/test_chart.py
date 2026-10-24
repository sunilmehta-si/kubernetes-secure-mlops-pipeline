import os
import tempfile
import unittest
from pathlib import Path

import yaml

from secure_mlops.helm import HelmError, pinned_version, template

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "charts/ticket-router"
MODEL = "kind-registry:5000/smlops/ticket-router-model@sha256:" + "a" * 64
SERVER = "kind-registry:5000/smlops-server@sha256:" + "b" * 64
IMAGES = {"model.image": MODEL, "server.image": SERVER}


def objects(text: str) -> dict[tuple[str, str], dict]:
    return {(d["kind"], d["metadata"]["name"]): d for d in yaml.safe_load_all(text) if d}


class ChartTests(unittest.TestCase):
    def test_every_object_says_what_made_it(self):
        for key, obj in objects(template(CHART, values=IMAGES)).items():
            with self.subTest(object=key):
                self.assertEqual(obj["metadata"]["labels"]["app.kubernetes.io/part-of"], "smlops")
                self.assertRegex(obj["metadata"]["labels"]["helm.sh/chart"], r"^ticket-router-\d+\.\d+\.\d+$")

    def test_no_render_without_both_images_by_digest(self):
        for values, message in (({"server.image": SERVER}, "model.image is required"),
                                ({**IMAGES, "model.image": MODEL.split("@")[0] + ":v4"}, "model/image"),
                                ({**IMAGES, "replicas.min": "3"}, "replicas")):
            with self.subTest(message=message), self.assertRaisesRegex(HelmError, message):
                template(CHART, values=values)

    def test_a_disruption_budget_only_where_a_replica_can_be_spared(self):
        # one replica (the defaults, dev, staging): a budget would block every drain instead of protecting anything
        self.assertNotIn(("PodDisruptionBudget", "ticket-router"), objects(template(CHART, values=IMAGES)))
        prod = objects(template(CHART, [ROOT / "deploy/gitops/envs/prod/values.yaml"], IMAGES))
        pdb = prod[("PodDisruptionBudget", "ticket-router")]
        self.assertEqual(pdb["spec"]["minAvailable"], 1)
        self.assertEqual(pdb["spec"]["selector"]["matchLabels"], {"serving.kserve.io/inferenceservice": "ticket-router"})

    def test_the_predictor_scales_on_cpu_between_min_and_max(self):
        pred = objects(template(CHART, values=IMAGES))[("InferenceService", "ticket-router")]["spec"]["predictor"]
        self.assertEqual((pred["scaleMetric"], pred["scaleTarget"]), ("cpu", 70))
        with self.assertRaisesRegex(HelmError, "autoscaling"):
            template(CHART, values={**IMAGES, "autoscaling.targetCpuUtilization": "150"})

    def test_prod_replicas_prefer_different_nodes_without_refusing_to_run(self):
        prod = objects(template(CHART, [ROOT / "deploy/gitops/envs/prod/values.yaml"], IMAGES))
        (spread,) = prod[("InferenceService", "ticket-router")]["spec"]["predictor"]["topologySpreadConstraints"]
        self.assertEqual((spread["topologyKey"], spread["whenUnsatisfiable"], spread["maxSkew"]),
                         ("kubernetes.io/hostname", "ScheduleAnyway", 1))
        dev = objects(template(CHART, [ROOT / "deploy/gitops/envs/dev/values.yaml"], IMAGES))
        self.assertNotIn("topologySpreadConstraints", dev[("InferenceService", "ticket-router")]["spec"]["predictor"])

    def test_another_helm_version_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d, "helm")
            fake.write_text("#!/bin/sh\necho v3.0.0+gdeadbeef\n")
            fake.chmod(0o755)
            old, os.environ["SMLOPS_HELM"] = os.environ.get("SMLOPS_HELM"), str(fake)
            try:
                with self.assertRaisesRegex(HelmError, f"rendered with {pinned_version()}"):
                    template(CHART, values=IMAGES)
            finally:
                os.environ.pop("SMLOPS_HELM") if old is None else os.environ.__setitem__("SMLOPS_HELM", old)

    def test_the_readme_documents_every_value_and_app_version_tracks_the_package(self):
        from secure_mlops import __version__
        values = yaml.safe_load((CHART / "values.yaml").read_text())

        def leaves(d, prefix=""):
            for k, v in d.items():
                if isinstance(v, dict) and k != "resources":
                    yield from leaves(v, f"{prefix}{k}.")
                else:
                    yield prefix + k
        readme = (CHART / "README.md").read_text()
        for key in leaves(values):
            with self.subTest(value=key):
                self.assertIn(f"| `{key}` |", readme)
        self.assertEqual(yaml.safe_load((CHART / "Chart.yaml").read_text())["appVersion"], __version__)


if __name__ == "__main__":
    unittest.main()
