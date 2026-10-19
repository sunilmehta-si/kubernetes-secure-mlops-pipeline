"""The deployment files: pinned versions, locked-down workloads, and policies that match the threat model."""
import re
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def versions() -> dict[str, str]:
    out = {}
    for line in (ROOT / "deploy/versions.env").read_text().splitlines():
        if line and not line.startswith("#"):
            key, _, value = line.partition("=")
            out[key] = value
    return out


class VersionTests(unittest.TestCase):
    def test_every_component_is_pinned_to_an_exact_release(self):
        v = versions()
        self.assertEqual(set(v), {"KIND_VERSION", "KIND_NODE_IMAGE", "CERT_MANAGER_VERSION", "KSERVE_VERSION",
                                  "KYVERNO_VERSION", "ZOT_VERSION", "COSIGN_VERSION"})
        for key, value in v.items():
            with self.subTest(key=key):
                self.assertRegex(value.split(":")[-1], r"^v\d+\.\d+\.\d+$")


def docs(path: str) -> list[dict]:
    return [d for d in yaml.safe_load_all((ROOT / path).read_text()) if d]


class ServingNamespaceTests(unittest.TestCase):
    def test_restricted_pod_security_and_no_api_token(self):
        ns, sa = docs("deploy/serving/namespace.yaml")
        self.assertEqual(ns["metadata"]["labels"]["pod-security.kubernetes.io/enforce"], "restricted")
        self.assertEqual((sa["kind"], sa["automountServiceAccountToken"]), ("ServiceAccount", False))
