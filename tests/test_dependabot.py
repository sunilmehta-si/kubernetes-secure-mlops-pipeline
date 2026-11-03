import unittest
from pathlib import Path

import yaml

from secure_mlops.versions import MIN_RELEASE_AGE_DAYS

ROOT = Path(__file__).resolve().parents[1]


class DependabotTests(unittest.TestCase):
    def setUp(self):
        self.updates = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())["updates"]

    def test_every_update_waits_as_long_as_a_pinned_release_does(self):
        for update in self.updates:
            with self.subTest(ecosystem=update["package-ecosystem"]):
                self.assertGreaterEqual(update.get("cooldown", {}).get("default-days", 0), MIN_RELEASE_AGE_DAYS)

    def test_the_lockfile_the_actions_and_the_base_image_are_updated(self):
        self.assertLessEqual({"uv", "github-actions", "docker"}, {u["package-ecosystem"] for u in self.updates})

    def test_no_ecosystem_and_directory_is_listed_twice(self):
        pairs = [(u["package-ecosystem"], u["directory"]) for u in self.updates]
        self.assertEqual(len(pairs), len(set(pairs)))      # Dependabot rejects a configuration that repeats one
