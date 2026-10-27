import unittest
from datetime import date
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

    def test_a_newer_release_waits_two_weeks_before_it_counts(self):
        upstream = {"cert-manager/cert-manager": versions.Release("v1.21.3", date(2026, 10, 23)),
                    "astral-sh/uv": versions.Release("0.13.6", date(2026, 10, 27)),
                    "kyverno/kyverno": versions.Release("v1.19.2", date(2026, 9, 30))}
        pins = {"CERT_MANAGER_VERSION": "v1.20.4", "UV_VERSION": "0.13.5", "KYVERNO_VERSION": "v1.19.1"}
        rows = {r.component: r for r in versions.report(pins, fetch=upstream.get, today=date(2026, 10, 27))}
        self.assertTrue(rows["CERT_MANAGER_VERSION"].too_new)
        self.assertEqual(rows["CERT_MANAGER_VERSION"].adoptable_on, date(2026, 11, 6))
        self.assertTrue(rows["UV_VERSION"].too_new)
        self.assertFalse(rows["KYVERNO_VERSION"].too_new)                              # 27 days old: adopt it
        text = versions.markdown(list(rows.values()))
        self.assertIn("3 of 3 behind upstream, 2 of them too new to adopt yet", text)
        self.assertIn("| v1.20.4 | v1.21.3 | minor behind; published 2026-10-23, adoptable from 2026-11-06 |", text)
        self.assertIn("| v1.19.1 | v1.19.2 | **patch behind** |", text)
        later = {r.component: r for r in versions.report(pins, fetch=upstream.get, today=date(2026, 11, 6))}
        self.assertFalse(later["CERT_MANAGER_VERSION"].too_new)                        # the day it turns 14 days old

    def test_a_release_without_a_date_is_never_held_back(self):
        rows = versions.report({"HELM_VERSION": "v4.3.0"}, fetch=lambda repo: "v4.3.1", today=date(2026, 10, 27))
        self.assertTrue(rows[0].behind)
        self.assertFalse(rows[0].too_new)

