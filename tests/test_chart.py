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


if __name__ == "__main__":
    unittest.main()
