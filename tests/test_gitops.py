import tempfile
import unittest
from pathlib import Path

import yaml

from secure_mlops.gitops import ORDER, ReleaseError, ci_argocd, promote, released_images, render

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


class PromotionTests(unittest.TestCase):
    def test_the_next_environment_gets_exactly_the_same_digests(self):
        with tempfile.TemporaryDirectory() as d:
            rendered = Path(d)
            render(MODEL, SERVER, rendered / "dev", "dev")
            promote("dev", "staging", rendered)
            promote("staging", "prod", rendered)
            self.assertEqual(released_images("prod", rendered), (MODEL, SERVER))
            prod = docs(rendered / "prod/inferenceservice.yaml")[0]
            self.assertEqual(prod["spec"]["predictor"]["minReplicas"], 2)       # prod's own values, same images

    def test_no_skipping_no_going_back_and_nothing_from_an_empty_environment(self):
        with tempfile.TemporaryDirectory() as d:
            rendered = Path(d)
            with self.assertRaisesRegex(ReleaseError, "dev has no release yet"):
                promote("dev", "staging", rendered)
            render(MODEL, SERVER, rendered / "dev", "dev")
            for source, target, message in (("dev", "prod", "prod is promoted from staging, not dev"),
                                            ("staging", "dev", "nothing is promoted to dev"),
                                            ("dev", "qa", "not on that path")):
                with self.subTest(source=source, target=target), self.assertRaisesRegex(ReleaseError, message):
                    promote(source, target, rendered)
            self.assertFalse((rendered / "prod").exists())

    def test_the_order_covers_every_environment(self):
        envs = sorted(p.name for p in (Path(__file__).resolve().parents[1] / "deploy/gitops/envs").iterdir())
        self.assertEqual(sorted(ORDER), envs)


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

    def test_only_the_source_changes_from_what_the_applicationset_and_project_say(self):
        from secure_mlops.gitops import application
        root = Path(__file__).resolve().parents[1]
        committed_project = ProjectTests().projects()["ml-serving-dev"]
        committed_app = application("dev")
        project, app = ci_argocd(self.SOURCE, "run-42")
        self.assertEqual((app["metadata"]["name"], app["spec"]["project"]), ("ticket-router-dev", "ml-serving-dev"))
        self.assertEqual(project["spec"]["sourceRepos"], [self.SOURCE])
        self.assertEqual(app["spec"]["source"], {"repoURL": self.SOURCE, "targetRevision": "run-42", "path": "."})
        for theirs, ours in ((committed_project, project), (committed_app, app)):
            for key in set(theirs["spec"]) - {"sourceRepos", "source"}:
                self.assertEqual(ours["spec"][key], theirs["spec"][key], key)
        self.assertEqual(committed_app["spec"]["source"]["path"], "deploy/gitops/rendered/dev")
        self.assertNotIn("{{", yaml.safe_dump(app))                          # every field filled in
        self.assertTrue((root / "deploy/gitops/argocd/applicationset.yaml").exists())

    def test_only_the_cluster_registry_and_plain_tags(self):
        for source, revision in (("oci://ghcr.io/someone/release", "v1"), ("https://kind-registry:5000/x", "v1"),
                                 (self.SOURCE, "v1; rm -rf /"), (self.SOURCE + "\n", "v1")):
            with self.subTest(source=source, revision=revision), self.assertRaises(ReleaseError):
                ci_argocd(source, revision)


if __name__ == "__main__":
    unittest.main()
