import tempfile
import unittest
from pathlib import Path

import yaml

from secure_mlops.gitops import ReleaseError, ci_argocd, render

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
            self.assertIn("--env prod", files[0].read_text().splitlines()[0])
            self.assertEqual(yaml.safe_load(Path(d, "target.yaml").read_text())["env"], "prod")   # not a resource
            isvc = docs(Path(d, "inferenceservice.yaml"))[0]
            self.assertEqual(isvc["spec"]["predictor"]["model"]["storageUri"], f"oci://{MODEL}")
            runtime = docs(Path(d, "servingruntime.yaml"))[0]
            self.assertEqual(runtime["spec"]["containers"][0]["image"], SERVER)
            kustomization = docs(Path(d, "kustomization.yaml"))[0]
            self.assertEqual(kustomization["resources"], [f.name for f in files[:-1]])
            self.assertEqual(kustomization["commonAnnotations"]["smlops.io/model-image"], MODEL)
            self.assertEqual(kustomization["commonAnnotations"]["smlops.io/environment"], "prod")
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
            with self.assertRaisesRegex(ReleaseError, "no environment 'qa'; environments are dev, prod, staging"):
                render(MODEL, SERVER, d, env="qa")
            self.assertEqual(list(Path(d).iterdir()), [])                   # nothing written on refusal

    def test_each_environment_gets_its_own_values(self):
        with tempfile.TemporaryDirectory() as d:
            replicas = {}
            for env in ("dev", "prod"):
                render(MODEL, SERVER, Path(d, env), env=env)
                isvc = docs(Path(d, env, "inferenceservice.yaml"))[0]
                replicas[env] = isvc["spec"]["predictor"]["minReplicas"]
            self.assertEqual(replicas, {"dev": 1, "prod": 2})


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

class ProjectTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def projects(self):
        return {p["metadata"]["name"]: p for p in docs(self.ROOT / "deploy/gitops/argocd/projects.yaml")}

    def test_one_fenced_project_per_environment_with_the_same_fence(self):
        projects = self.projects()
        self.assertEqual(sorted(projects), ["ml-serving-dev", "ml-serving-prod", "ml-serving-staging"])
        fences = {name: {k: v for k, v in p["spec"].items() if k not in ("description", "syncWindows")}
                  for name, p in projects.items()}
        self.assertEqual(fences["ml-serving-dev"], fences["ml-serving-staging"])
        self.assertEqual(fences["ml-serving-dev"], fences["ml-serving-prod"])
        for name, project in projects.items():
            with self.subTest(project=name):
                self.assertEqual(project["spec"]["clusterResourceWhitelist"], [])
                self.assertEqual([d["namespace"] for d in project["spec"]["destinations"]], ["serving"])

    def test_every_kind_a_release_contains_is_allowed_and_nothing_more(self):
        allowed = {(k["group"], k["kind"])
                   for k in self.projects()["ml-serving-prod"]["spec"]["namespaceResourceWhitelist"]}
        with tempfile.TemporaryDirectory() as d:
            files = render(MODEL, SERVER, d)
            rendered = {(doc["apiVersion"].rpartition("/")[0], doc["kind"]) for f in files[:-1] for doc in docs(f)}
        self.assertEqual(rendered, allowed)

    def test_only_prod_has_sync_windows_and_hand_syncs_stay_possible(self):
        projects = self.projects()
        self.assertNotIn("syncWindows", projects["ml-serving-dev"]["spec"])
        self.assertNotIn("syncWindows", projects["ml-serving-staging"]["spec"])
        (window,) = projects["ml-serving-prod"]["spec"]["syncWindows"]
        self.assertEqual((window["kind"], window["schedule"], window["manualSync"]), ("allow", "0 8 * * 1-5", True))
        targets = {e: yaml.safe_load((self.ROOT / f"deploy/gitops/envs/{e}/target.yaml").read_text())["project"]
                   for e in ("dev", "staging", "prod")}
        self.assertEqual(set(targets.values()), set(projects))                 # every target names a real project


class ApplicationSetTests(unittest.TestCase):
    ROOT = Path(__file__).resolve().parents[1]

    def appset(self):
        (appset,) = docs(self.ROOT / "deploy/gitops/argocd/applicationset.yaml")
        return appset

    def test_one_application_per_rendered_environment_in_its_own_directory_and_project(self):
        spec = self.appset()["spec"]
        (gen,) = spec["generators"]
        self.assertEqual(gen["git"]["files"], [{"path": "deploy/gitops/rendered/*/target.yaml"}])
        template = spec["template"]["spec"]
        self.assertEqual(template["project"], "{{ .project }}")
        self.assertEqual(template["source"]["path"], "deploy/gitops/rendered/{{ .env }}")
        self.assertEqual((template["source"]["targetRevision"], gen["git"]["revision"]), ("main", "main"))
        self.assertEqual(template["syncPolicy"]["automated"], {"prune": True, "selfHeal": True})
        self.assertIn("missingkey=error", spec["goTemplateOptions"])

    def test_the_template_reads_only_fields_every_target_has(self):
        import re
        fields = set(re.findall(r"\{\{ \.(\w+) \}\}", (self.ROOT / "deploy/gitops/argocd/applicationset.yaml")
                                .read_text()))
        for env in ("dev", "staging", "prod"):
            target = yaml.safe_load((self.ROOT / f"deploy/gitops/envs/{env}/target.yaml").read_text())
            self.assertLessEqual(fields, set(target), env)

    def test_cli_default_output_is_the_directory_the_applicationset_reads(self):
        from secure_mlops.cli import _parser
        args = _parser().parse_args(["render-release", "--env", "staging", "--model-image", MODEL,
                                     "--server-image", SERVER])
        self.assertEqual(args.output, None)                    # resolved per environment by the command
        from secure_mlops.gitops import release_dir
        self.assertEqual(release_dir("staging"), self.ROOT / "deploy/gitops/rendered/staging")


class CiArgoCdTests(unittest.TestCase):
    SOURCE = "oci://kind-registry:5000/smlops/release"

    def test_only_the_source_changes_from_the_committed_manifests(self):
        committed = ArgoCdTests().manifests()
        project, app = ci_argocd(self.SOURCE, "run-42")
        self.assertEqual(project["spec"]["sourceRepos"], [self.SOURCE])
        self.assertEqual(app["spec"]["source"], {"repoURL": self.SOURCE, "targetRevision": "run-42", "path": "."})
        for theirs, ours in zip(committed, (project, app)):
            for key in set(theirs["spec"]) - {"sourceRepos", "source"}:
                self.assertEqual(ours["spec"][key], theirs["spec"][key], key)
        self.assertEqual(committed[0]["spec"]["sourceRepos"][0][:8], "https://")   # the committed file is untouched

    def test_only_the_cluster_registry_and_plain_tags(self):
        for source, revision in (("oci://ghcr.io/someone/release", "v1"), ("https://kind-registry:5000/x", "v1"),
                                 (self.SOURCE, "v1; rm -rf /"), (self.SOURCE + "\n", "v1")):
            with self.subTest(source=source, revision=revision), self.assertRaises(ReleaseError):
                ci_argocd(source, revision)


if __name__ == "__main__":
    unittest.main()
