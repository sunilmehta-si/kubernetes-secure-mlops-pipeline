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



def render(path: str, **values) -> list[dict]:
    from string import Template
    text = Template((ROOT / path).read_text()).substitute(values)
    return [d for d in yaml.safe_load_all(text) if d]


DIGEST = "registry.local/x@sha256:" + "a" * 64


class ServingRuntimeTests(unittest.TestCase):
    def test_runs_smlops_serve_with_the_key_mounted_read_only(self):
        (rt,) = render("deploy/serving/servingruntime.yaml", SERVER_IMAGE=DIGEST)
        c = rt["spec"]["containers"][0]
        self.assertEqual(c["image"], DIGEST)
        self.assertEqual(c["args"][:3], ["serve", "--bundle", "/mnt/models"])
        key = c["args"][c["args"].index("--public-key") + 1]
        mount = next(m for m in c["volumeMounts"] if m["name"] == "verify-key")
        self.assertTrue(key.startswith(mount["mountPath"]) and mount["readOnly"])

    def test_container_meets_pod_security_restricted(self):
        (rt,) = render("deploy/serving/servingruntime.yaml", SERVER_IMAGE=DIGEST)
        sc = rt["spec"]["containers"][0]["securityContext"]
        self.assertEqual((sc["runAsNonRoot"], sc["allowPrivilegeEscalation"], sc["readOnlyRootFilesystem"]),
                         (True, False, True))
        self.assertEqual((sc["capabilities"]["drop"], sc["seccompProfile"]["type"]), (["ALL"], "RuntimeDefault"))
        self.assertIn("limits", rt["spec"]["containers"][0]["resources"])



class InferenceServiceTests(unittest.TestCase):
    def test_model_by_digest_on_the_bundle_runtime_without_a_token(self):
        (isvc,) = render("deploy/serving/inferenceservice.yaml", MODEL_IMAGE=DIGEST)
        pred = isvc["spec"]["predictor"]
        self.assertEqual(pred["model"]["storageUri"], f"oci://{DIGEST}")
        self.assertRegex(pred["model"]["storageUri"], r"@sha256:[0-9a-f]{64}$")
        (rt,) = render("deploy/serving/servingruntime.yaml", SERVER_IMAGE=DIGEST)
        self.assertEqual(pred["model"]["runtime"], rt["metadata"]["name"])
        self.assertEqual(pred["model"]["modelFormat"]["name"], rt["spec"]["supportedModelFormats"][0]["name"])
        (_, sa) = docs("deploy/serving/namespace.yaml")
        self.assertEqual(pred["serviceAccountName"], sa["metadata"]["name"])
