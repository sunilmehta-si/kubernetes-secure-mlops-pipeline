import re
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==([^\s;]+)")


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def lock() -> dict[str, str]:
    pins = {}
    for line in (ROOT / "requirements-lock.txt").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            match = PIN.match(line)
            assert match, f"not an exact pin: {line}"
            pins[canonical(match[1])] = match[2]
    return pins


class LockTests(unittest.TestCase):
    def test_every_direct_dependency_is_locked_at_its_pinned_version(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
        direct = project["dependencies"] + [d for deps in project["optional-dependencies"].values() for d in deps]
        locked = lock()
        for requirement in direct:
            name, version = PIN.match(requirement).groups()
            with self.subTest(dependency=name):
                self.assertEqual(locked.get(canonical(name)), version)

    def test_the_package_that_broke_installs_overnight_is_locked(self):
        self.assertIn("pyopenssl", lock())
        self.assertIn("sigstore", lock())

    def test_every_install_uses_the_lock(self):
        places = [*(ROOT / ".github/workflows").glob("*.yml"), ROOT / "Dockerfile"]
        for path in places:
            for line in path.read_text().splitlines():
                if re.search(r"pip install (-e |\.)", line) or re.search(r'pip install -e "', line):
                    with self.subTest(file=path.name, line=line.strip()):
                        self.assertIn("-c requirements-lock.txt", line)


if __name__ == "__main__":
    unittest.main()
