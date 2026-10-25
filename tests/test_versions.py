import unittest
from pathlib import Path

from secure_mlops import versions

ROOT = Path(__file__).resolve().parents[1]


class VersionsTests(unittest.TestCase):
    def test_every_release_pin_in_versions_env_is_mapped_to_its_repository(self):
        pins = versions.load_pins(ROOT / "deploy/versions.env")
        release_pins = {k for k, v in pins.items() if versions.SEMVER.match(v)}
        self.assertEqual(release_pins - set(versions.COMPONENTS), set(), "add the component's repository to COMPONENTS")
        self.assertEqual(set(versions.COMPONENTS) - set(pins), set(), "COMPONENTS names a pin that versions.env lost")

    def test_compare_names_the_largest_part_that_moved(self):
        self.assertEqual(versions.compare("v1.2.3", "v1.2.3"), "current")
        self.assertEqual(versions.compare("v1.2.3", "v1.2.4"), "patch")
        self.assertEqual(versions.compare("v1.2.3", "v1.3.0"), "minor")
        self.assertEqual(versions.compare("v1.2.3", "v2.0.0"), "major")
        self.assertEqual(versions.compare("0.13.5", "0.13.4"), "current")             # a pin ahead of "latest" is fine
        with self.assertRaises(versions.VersionsError):
            versions.compare("v1.2", "v1.2.3")

    def test_report_and_markdown_from_a_fake_upstream(self):
        latest = {"kyverno/kyverno": "v1.19.2", "helm/helm": "v4.3.1", "astral-sh/uv": "0.13.5"}
        pins = {"KYVERNO_VERSION": "v1.19.1", "HELM_VERSION": "v4.3.1", "UV_VERSION": "0.13.5", "REGISTRY": "kind-registry:5000"}
        rows = versions.report(pins, fetch=lambda repo: latest[repo])
        self.assertEqual([(r.component, r.status) for r in rows],
                         [("KYVERNO_VERSION", "patch"), ("HELM_VERSION", "current"), ("UV_VERSION", "current")])
        text = versions.markdown(rows)
        self.assertIn("1 of 3 behind upstream", text)
        self.assertIn("| KYVERNO_VERSION ([kyverno/kyverno](https://github.com/kyverno/kyverno/releases)) | v1.19.1 | v1.19.2 | **patch behind** |", text)
        self.assertIn("all 2 current", versions.markdown([r for r in rows if not r.behind]))

    def test_a_failing_upstream_is_an_error_not_a_silent_current(self):
        def fetch(repo):
            raise versions.VersionsError(f"{repo}: boom")
        with self.assertRaises(versions.VersionsError):
            versions.report({"HELM_VERSION": "v4.3.1"}, fetch=fetch)
