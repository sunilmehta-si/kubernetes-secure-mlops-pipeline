import re
import subprocess
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==([^\s;]+)")


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def locked() -> dict[str, str]:
    """{package: version} from uv.lock."""
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    return {canonical(p["name"]): p["version"] for p in lock["package"] if "version" in p}


class LockTests(unittest.TestCase):
    def test_every_direct_dependency_is_locked_at_its_pinned_version(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())
        direct = project["project"]["dependencies"] + [
            d for deps in project["project"]["optional-dependencies"].values() for d in deps]
        versions = locked()
        for requirement in direct:
            name, version = PIN.match(requirement).groups()
            with self.subTest(dependency=name):
                self.assertEqual(versions.get(canonical(name)), version)

    def test_the_cve_override_and_the_undeclared_import_are_in_the_lock(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())
        self.assertIn("urllib3==2.8.0", project["tool"]["uv"]["override-dependencies"])   # kfp pins 2.7.0
        versions = locked()
        self.assertEqual(versions["urllib3"], "2.8.0")
        self.assertIn("pyopenssl", versions)                       # model-signing imports it undeclared

    def test_every_install_uses_the_frozen_lock(self):
        places = [*(ROOT / ".github/workflows").glob("*.yml"), ROOT / "Dockerfile"]
        for path in places:
            text = path.read_text()
            with self.subTest(file=path.name):
                self.assertNotRegex(text, r"pip install (-c |-e |\.)", "installs must go through uv.lock")
                if "uv sync" in text:
                    for line in text.splitlines():
                        if "uv sync" in line:
                            self.assertIn("--frozen", line, line.strip())
        self.assertFalse((ROOT / "requirements-lock.txt").exists())

    def test_the_image_and_ci_install_with_the_same_uv(self):
        versions = dict(line.split("=", 1) for line in (ROOT / "deploy/versions.env").read_text().splitlines()
                        if "=" in line and not line.startswith("#"))
        self.assertRegex((ROOT / "Dockerfile").read_text(),
                         rf"ghcr.io/astral-sh/uv:{re.escape(versions['UV_VERSION'])}@sha256:[0-9a-f]{{64}} ")
        self.assertIn("${UV_VERSION}", (ROOT / ".github/actions/setup-uv/action.yml").read_text())

    def test_the_lock_matches_pyproject(self):
        """What CI runs as `uv lock --check`; skipped where uv is not installed."""
        import shutil
        uv = shutil.which("uv") or str(Path.home() / ".local/bin/uv")
        if not Path(uv).exists():
            self.skipTest("uv is not installed here; CI runs uv lock --check")
        run = subprocess.run([uv, "lock", "--check", "--offline"], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)


if __name__ == "__main__":
    unittest.main()
