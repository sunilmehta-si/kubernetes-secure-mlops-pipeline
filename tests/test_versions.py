import json
import os
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from secure_mlops import versions

ROOT = Path(__file__).resolve().parents[1]


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, *args):
        return json.dumps(self.payload).encode()


def serving(routes):
    """A urlopen stand-in: the payload whose path ends the requested URL, else an unreachable API. Also returns the requests seen."""
    seen = []

    def urlopen(request, timeout=None):
        seen.append(request)
        for suffix, payload in routes.items():
            if request.full_url.endswith(suffix):
                return FakeResponse(payload)
        raise OSError(f"no route for {request.full_url}")
    return urlopen, seen


class GithubApiTests(unittest.TestCase):
    LATEST = {"tag_name": "v1.2.3", "published_at": "2026-10-20T08:00:00Z"}
    LISTING = [LATEST]

    def read(self, routes, env=None):
        urlopen, seen = serving(routes)
        with mock.patch.dict(os.environ, env or {}, clear=True), mock.patch.object(versions.urllib.request, "urlopen", urlopen):
            return versions.github_latest("o/r"), seen

    def test_the_latest_release_carries_its_tag_and_publish_date(self):
        release, _ = self.read({"/repos/o/r/releases?per_page=50": self.LISTING})
        self.assertEqual(release, versions.Release("v1.2.3", date(2026, 10, 20)))

    def test_a_release_candidate_marked_latest_is_not_the_latest_release(self):
        rc = {"tag_name": "v0.33.0rc3", "published_at": "2026-11-02T08:00:00Z", "prerelease": False}
        stable = {"tag_name": "v0.32.0", "published_at": "2026-10-18T08:00:00Z", "prerelease": False}
        older = {"tag_name": "v0.31.0", "published_at": "2026-10-05T08:00:00Z", "prerelease": False}
        release, _ = self.read({"/releases?per_page=50": [rc, stable, older]})
        self.assertEqual(release, versions.Release("v0.32.0", date(2026, 10, 18)))

    def test_drafts_and_prereleases_are_skipped(self):
        listing = [{"tag_name": "v2.0.0", "published_at": "2026-10-30T08:00:00Z", "draft": True},
                   {"tag_name": "v1.9.0", "published_at": "2026-10-29T08:00:00Z", "prerelease": True},
                   {"tag_name": "v1.8.0", "published_at": "2026-10-01T08:00:00Z"}]
        release, _ = self.read({"/releases?per_page=50": listing})
        self.assertEqual(release.tag, "v1.8.0")

    def test_the_highest_version_wins_over_a_later_backport(self):
        listing = [{"tag_name": "v1.20.5", "published_at": "2026-11-01T08:00:00Z"},
                   {"tag_name": "v1.21.0", "published_at": "2026-10-20T08:00:00Z"}]
        release, _ = self.read({"/releases?per_page=50": listing})
        self.assertEqual(release.tag, "v1.21.0")

    def test_a_repository_without_a_plain_release_falls_back_to_the_latest_endpoint(self):
        listing = [{"tag_name": "release-2026.10", "published_at": "2026-10-01T08:00:00Z"}]
        release, seen = self.read({"/releases?per_page=50": listing, "/releases/latest": self.LATEST})
        self.assertEqual(release.tag, "v1.2.3")
        self.assertEqual(len(seen), 2)

    def test_an_unexpected_answer_is_a_versions_error(self):
        urlopen, _ = serving({"/releases?per_page=50": {"message": "Not Found"}})
        with mock.patch.object(versions.urllib.request, "urlopen", urlopen):
            with self.assertRaisesRegex(versions.VersionsError, "latest release"):
                versions.github_latest("o/r")

    def test_a_token_raises_the_rate_limit_and_none_sends_no_header(self):
        _, seen = self.read({"/releases?per_page=50": self.LISTING}, env={"GH_TOKEN": "t0k"})
        self.assertEqual(seen[0].get_header("Authorization"), "Bearer t0k")
        _, seen = self.read({"/releases?per_page=50": self.LISTING})
        self.assertIsNone(seen[0].get_header("Authorization"))

    def test_the_release_notes_come_back_as_text(self):
        urlopen, _ = serving({"/releases/tags/v1.2.3": {"body": "notes"}})
        with mock.patch.object(versions.urllib.request, "urlopen", urlopen):
            self.assertEqual(versions.github_release_body("o/r", "v1.2.3"), "notes")

    def test_an_unreachable_api_is_a_versions_error_for_both_lookups(self):
        urlopen, _ = serving({})
        with mock.patch.object(versions.urllib.request, "urlopen", urlopen):
            with self.assertRaisesRegex(versions.VersionsError, "latest release"):
                versions.github_latest("o/r")
            with self.assertRaisesRegex(versions.VersionsError, "release notes"):
                versions.github_release_body("o/r", "v1.2.3")


class VersionsTests(unittest.TestCase):
    def test_every_release_pin_in_versions_env_is_mapped_to_its_repository(self):
        pins = versions.load_pins(ROOT / "deploy/versions.env")
        release_pins = {k for k, v in pins.items() if versions.SEMVER.match(v)}
        self.assertEqual(release_pins - set(versions.COMPONENTS), set(), "add the component's repository to COMPONENTS")
        self.assertEqual(set(versions.COMPONENTS) - set(pins), set(), "COMPONENTS names a pin that versions.env lost")

    def test_every_pin_is_compared_or_says_why_not(self):
        # a new line in versions.env has to be mapped to the repository that releases it, or listed with its reason
        pins = versions.load_pins(ROOT / "deploy/versions.env")
        self.assertEqual(set(pins), set(versions.COMPONENTS) | set(versions.NOT_COMPARED))
        self.assertEqual(set(versions.COMPONENTS) & set(versions.NOT_COMPARED), set())

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
        self.assertIn("Not compared: KIND_NODE_IMAGE (pinned by digest", text)     # the reader sees what the table leaves out

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

    def test_json_spells_out_each_decision(self):
        upstream = {"cert-manager/cert-manager": versions.Release("v1.21.3", date(2026, 10, 23)),
                    "kyverno/kyverno": versions.Release("v1.19.2", date(2026, 9, 30)),
                    "helm/helm": versions.Release("v4.3.1", date(2026, 9, 1))}
        pins = {"CERT_MANAGER_VERSION": "v1.20.4", "KYVERNO_VERSION": "v1.19.1", "HELM_VERSION": "v4.3.1"}
        data = {d["component"]: d for d in versions.as_json(versions.report(pins, fetch=upstream.get, today=date(2026, 10, 27)))}
        self.assertEqual(data["CERT_MANAGER_VERSION"], {
            "component": "CERT_MANAGER_VERSION", "repository": "cert-manager/cert-manager", "pinned": "v1.20.4",
            "latest": "v1.21.3", "status": "minor", "published": "2026-10-23", "adoptable_on": "2026-11-06",
            "too_new": True, "adopt_now": False})
        self.assertTrue(data["KYVERNO_VERSION"]["adopt_now"])
        self.assertFalse(data["HELM_VERSION"]["adopt_now"])                             # current: nothing to adopt

    def test_a_release_without_a_date_is_never_held_back(self):
        rows = versions.report({"HELM_VERSION": "v4.3.0"}, fetch=lambda repo: "v4.3.1", today=date(2026, 10, 27))
        self.assertTrue(rows[0].behind)
        self.assertFalse(rows[0].too_new)


class NodeImageTests(unittest.TestCase):
    NOTES = ("Images pre-built for this release:\n* v1.37.0: `kindest/node:v1.37.0@sha256:" + "a" * 64 + "`\n"
             "* v1.36.4: `kindest/node:v1.36.4@sha256:" + "b" * 64 + "`\n")

    def test_the_pinned_node_image_must_be_one_the_kind_release_lists(self):
        pins = {"KIND_VERSION": "v0.33.0", "KIND_NODE_IMAGE": "kindest/node:v1.37.0@sha256:" + "a" * 64}
        seen = []
        def body(repo, tag):
            seen.append((repo, tag)); return self.NOTES
        self.assertTrue(versions.node_image_listed(pins, body))
        self.assertEqual(seen, [("kubernetes-sigs/kind", "v0.33.0")])
        pins["KIND_NODE_IMAGE"] = "kindest/node:v1.37.0@sha256:" + "c" * 64      # same tag, another release's build
        self.assertFalse(versions.node_image_listed(pins, body))

    def test_the_committed_pins_pass_against_the_real_notes_shape(self):
        pins = versions.load_pins(ROOT / "deploy/versions.env")
        notes = f"* {pins['KIND_NODE_IMAGE'].split(':')[1].split('@')[0]}: `{pins['KIND_NODE_IMAGE']}`"
        self.assertTrue(versions.node_image_listed(pins, lambda repo, tag: notes))

    def test_json_carries_the_node_image_check_as_a_final_object(self):
        rows = versions.report({"HELM_VERSION": "v4.3.1"}, fetch=lambda repo: "v4.3.1")
        self.assertEqual(versions.as_json(rows, node_image_ok=False)[-1],
                         {"component": "KIND_NODE_IMAGE", "check": "listed in the pinned kind release's notes", "ok": False})
        self.assertEqual(len(versions.as_json(rows)), 1)

    def test_markdown_says_whether_the_node_image_is_listed(self):
        rows = versions.report({"HELM_VERSION": "v4.3.1"}, fetch=lambda repo: "v4.3.1")
        self.assertIn("Node image: listed, digest and all", versions.markdown(rows, node_image_ok=True))
        self.assertIn("**not among the images", versions.markdown(rows, node_image_ok=False))
        self.assertNotIn("Node image:", versions.markdown(rows))
