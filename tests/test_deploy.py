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
                                  "KYVERNO_VERSION", "ZOT_VERSION", "COSIGN_VERSION", "PROMETHEUS_VERSION", "ALERTMANAGER_VERSION", "ARGOCD_VERSION",
                                  "ORAS_VERSION", "HELM_VERSION", "REGISTRY",
                                  "KIND_CONTEXT"})
        self.assertEqual(v.pop("REGISTRY"), "kind-registry:5000")
        self.assertEqual(v.pop("KIND_CONTEXT"), "kind-smlops")                       # kind-<name in kind.yaml>
        policy = (ROOT / "deploy/policy/require-digests.yaml").read_text()
        self.assertIn('"kind-registry:5000/*@sha256:*"', policy)                     # policy and scripts agree
        for key, value in v.items():
            with self.subTest(key=key):
                self.assertRegex(value.split(":")[-1], r"^v\d+\.\d+\.\d+$")


def docs(path: str) -> list[dict]:
    return [d for d in yaml.safe_load_all((ROOT / path).read_text()) if d]


class ServingNamespaceTests(unittest.TestCase):
    def test_restricted_pod_security_and_no_api_token(self):
        (ns,), (sa,) = docs("deploy/serving/namespace.yaml"), docs("deploy/serving/serviceaccount.yaml")
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
        keys_file = c["args"][c["args"].index("--api-keys") + 1]
        api = next(m for m in c["volumeMounts"] if m["name"] == "api-keys")
        self.assertTrue(keys_file.startswith(api["mountPath"]) and api["readOnly"])
        self.assertEqual(next(v for v in rt["spec"]["volumes"] if v["name"] == "api-keys")["secret"]["secretName"],
                         "smlops-api-keys")
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
        self.assertEqual(isvc["metadata"]["annotations"]["serving.kserve.io/deploymentMode"], "Standard")
        self.assertEqual(pred["model"]["storageUri"], f"oci://{DIGEST}")
        self.assertRegex(pred["model"]["storageUri"], r"@sha256:[0-9a-f]{64}$")
        (rt,) = render("deploy/serving/servingruntime.yaml", SERVER_IMAGE=DIGEST)
        self.assertEqual(pred["model"]["runtime"], rt["metadata"]["name"])
        self.assertEqual(pred["model"]["modelFormat"]["name"], rt["spec"]["supportedModelFormats"][0]["name"])
        (sa,) = docs("deploy/serving/serviceaccount.yaml")
        self.assertEqual(pred["serviceAccountName"], sa["metadata"]["name"])



class NetworkPolicyTests(unittest.TestCase):
    def test_default_deny_both_ways_and_one_narrow_ingress(self):
        deny, allow = docs("deploy/serving/networkpolicy.yaml")
        self.assertEqual((deny["spec"]["podSelector"], sorted(deny["spec"]["policyTypes"])), ({}, ["Egress", "Ingress"]))
        self.assertNotIn("ingress", deny["spec"])
        self.assertNotIn("egress", deny["spec"])
        self.assertEqual(allow["spec"]["policyTypes"], ["Ingress"])                 # no egress is ever allowed
        (rule,) = allow["spec"]["ingress"]
        self.assertEqual(rule["ports"], [{"protocol": "TCP", "port": 8080}])
        self.assertEqual(rule["from"][0]["namespaceSelector"]["matchLabels"], {"smlops.io/model-clients": "true"})



class SignaturePolicyTests(unittest.TestCase):
    def test_every_serving_image_needs_the_pipeline_signature_and_failures_block(self):
        (policy,) = docs("deploy/policy/verify-signatures.yaml")
        self.assertEqual((policy["spec"]["failurePolicy"], policy["spec"]["background"]), ("Fail", False))
        (rule,) = policy["spec"]["rules"]
        self.assertEqual(rule["match"]["any"][0]["resources"]["namespaces"], ["serving"])
        (check,) = rule["verifyImages"]
        self.assertEqual((check["imageReferences"], check["failureAction"], check["required"], check["verifyDigest"]),
                         (["*"], "Enforce", True, True))
        key = check["attestors"][0]["entries"][0]["keys"]
        self.assertEqual(key["secret"], {"name": "smlops-signing-key", "namespace": "kyverno"})



class DigestPolicyTests(unittest.TestCase):
    def test_containers_and_init_containers_need_the_registry_and_a_digest(self):
        import fnmatch
        (policy,) = docs("deploy/policy/require-digests.yaml")
        (rule,) = policy["spec"]["rules"]
        self.assertEqual(rule["validate"]["failureAction"], "Enforce")
        spec = rule["validate"]["pattern"]["spec"]
        for key in ("containers", "=(initContainers)"):
            pattern = spec[key][0]["image"]
            self.assertTrue(fnmatch.fnmatch("kind-registry:5000/ticket-router@sha256:" + "a" * 64, pattern))
            for bad in ("kind-registry:5000/ticket-router:latest", "docker.io/library/busybox@sha256:" + "a" * 64):
                self.assertFalse(fnmatch.fnmatch(bad, pattern), bad)



class KindTests(unittest.TestCase):
    def test_containerd_reads_per_registry_hosts(self):
        (cluster,) = docs("deploy/kind.yaml")
        self.assertEqual((cluster["kind"], cluster["name"]), ("Cluster", "smlops"))
        self.assertIn('config_path = "/etc/containerd/certs.d"', cluster["containerdConfigPatches"][0])
