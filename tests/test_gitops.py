import tempfile
import unittest
from pathlib import Path

import yaml

from secure_mlops.gitops import ReleaseError, render

MODEL = "kind-registry:5000/smlops/ticket-router-model@sha256:" + "a" * 64
SERVER = "kind-registry:5000/smlops/server@sha256:" + "b" * 64


def docs(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


class RenderTests(unittest.TestCase):
    def test_renders_the_serving_manifests_with_both_images_by_digest(self):
        with tempfile.TemporaryDirectory() as d:
            files = render(MODEL, SERVER, d)
            self.assertEqual([f.name for f in files], ["inferenceservice.yaml", "namespace.yaml",
                                                       "networkpolicy.yaml", "servingruntime.yaml",
                                                       "kustomization.yaml"])
            isvc = docs(Path(d, "inferenceservice.yaml"))[0]
            self.assertEqual(isvc["spec"]["predictor"]["model"]["storageUri"], f"oci://{MODEL}")
            runtime = docs(Path(d, "servingruntime.yaml"))[0]
            self.assertEqual(runtime["spec"]["containers"][0]["image"], SERVER)
            kustomization = docs(Path(d, "kustomization.yaml"))[0]
            self.assertEqual(kustomization["resources"], [f.name for f in files[:-1]])
            self.assertEqual(kustomization["commonAnnotations"]["smlops.io/model-image"], MODEL)
            for f in files:
                self.assertNotIn("${", f.read_text(), f.name)

    def test_rendering_is_deterministic(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            render(MODEL, SERVER, a)
            render(MODEL, SERVER, b)
            for f in Path(a).iterdir():
                self.assertEqual(f.read_text(), Path(b, f.name).read_text())

    def test_tags_and_foreign_registries_are_refused(self):
        with tempfile.TemporaryDirectory() as d:
            for model, server, message in [
                ("kind-registry:5000/smlops/ticket-router-model:v4", SERVER, "pinned by digest"),
                (MODEL, "docker.io/someone/server@sha256:" + "b" * 64, "cluster registry"),
                (MODEL, SERVER + "\nimage: evil", "pinned by digest"),
                (MODEL + "\n", SERVER, "pinned by digest"),                    # $ alone would allow this
            ]:
                with self.subTest(message=message), self.assertRaisesRegex(ReleaseError, message):
                    render(model, server, d)
            self.assertEqual(list(Path(d).iterdir()), [])                   # nothing written on refusal


if __name__ == "__main__":
    unittest.main()
