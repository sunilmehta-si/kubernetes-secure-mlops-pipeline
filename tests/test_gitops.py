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
            self.assertEqual([f.name for f in files], ["inferenceservice.yaml", "networkpolicy.yaml",
                                                       "serviceaccount.yaml", "servingruntime.yaml",
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


class ArgoCdTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def manifests(self):
        project, app = docs(self.ROOT / "deploy/gitops/argocd/ticket-router.yaml")
        return project, app

    def test_every_kind_a_release_contains_is_allowed_and_nothing_cluster_scoped(self):
        project, _ = self.manifests()
        allowed = {(k["group"], k["kind"]) for k in project["spec"]["namespaceResourceWhitelist"]}
        self.assertEqual(project["spec"]["clusterResourceWhitelist"], [])
        with tempfile.TemporaryDirectory() as d:
            files = render(MODEL, SERVER, d)
            rendered = {(doc["apiVersion"].rpartition("/")[0], doc["kind"]) for f in files[:-1] for doc in docs(f)}
            namespaces = {doc["metadata"]["namespace"] for f in files[:-1] for doc in docs(f)}
        self.assertLessEqual(rendered, allowed)
        self.assertEqual(rendered, allowed, "the project allows kinds no release uses")
        self.assertEqual(namespaces, {"serving"})

    def test_the_application_syncs_rendered_releases_from_main_into_serving_only(self):
        project, app = self.manifests()
        self.assertEqual(app["spec"]["project"], project["metadata"]["name"])
        self.assertEqual((app["spec"]["source"]["targetRevision"], app["spec"]["source"]["path"]),
                         ("main", "deploy/gitops/production"))
        self.assertEqual(project["spec"]["sourceRepos"], [app["spec"]["source"]["repoURL"]])
        self.assertEqual([d["namespace"] for d in project["spec"]["destinations"]], ["serving"])
        self.assertEqual(app["spec"]["destination"]["namespace"], "serving")
        self.assertEqual(app["spec"]["syncPolicy"]["automated"], {"prune": True, "selfHeal": True})

    def test_cli_default_output_is_the_path_argo_cd_syncs(self):
        from secure_mlops.cli import _parser
        args = _parser().parse_args(["render-release", "--model-image", MODEL, "--server-image", SERVER])
        self.assertEqual(args.output, self.manifests()[1]["spec"]["source"]["path"])


if __name__ == "__main__":
    unittest.main()
