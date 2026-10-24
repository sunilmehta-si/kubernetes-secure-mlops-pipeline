import base64
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from secure_mlops.attest import BUILD_TYPE, PREDICATE_TYPE
from secure_mlops.images import BOM_TYPE, ImageError, verify_image

MODEL = "kind-registry:5000/ticket-router@sha256:" + "a" * 64
SERVER = "kind-registry:5000/smlops-server@sha256:" + "b" * 64


def envelope(predicate_type, predicate):
    statement = {"_type": "https://in-toto.io/Statement/v1", "predicateType": predicate_type,
                 "subject": [{"name": "x", "digest": {"sha256": "a" * 64}}], "predicate": predicate}
    return json.dumps({"payloadType": "application/vnd.in-toto+json",
                       "payload": base64.b64encode(json.dumps(statement).encode()).decode(), "signatures": []})


def provenance(kind, build_type=BUILD_TYPE, model="ticket-router", version="4"):
    params = {"kind": kind, **({"model": model, "version": version} if kind == "model" else {})}
    return {"buildDefinition": {"buildType": build_type, "externalParameters": params,
                                "resolvedDependencies": [{"uri": "git+x", "digest": {"gitCommit": "c" * 40}}]},
            "runDetails": {"builder": {"id": "local"}}}


class FakeCosign:
    """A cosign on PATH that answers from canned attestations and records what it was asked."""

    def __init__(self, directory: Path, attestations: dict[str, list[str]], fail_verify=False):
        directory = Path(tempfile.mkdtemp(dir=directory))          # one script per fake, so cases never mix
        self.calls = directory / "calls.log"
        script = directory / "cosign"
        lines = ["#!/bin/sh", f'echo "$@" >> "{self.calls}"', 'case "$1" in',
                 f'  verify) {"echo verify failed >&2; exit 1" if fail_verify else "exit 0"} ;;',
                 "  verify-attestation)", '    for a in "$@"; do case "$prev" in --type) type="$a";; esac; prev="$a"; done']
        for kind, envs in attestations.items():
            body = "; ".join(f"printf '%s\\n' '{e}'" for e in envs) or "true"
            lines.append(f'    [ "$type" = {kind} ] && {{ {body}; exit 0; }}')
        lines += ["    exit 0 ;;", "esac"]
        script.write_text("\n".join(lines) + "\n")
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        self.path = str(script)


class VerifyImageTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.key = Path(self.dir.name, "cosign.pub")
        self.key.write_text("key")

    def tearDown(self):
        self.dir.cleanup()

    def cosign(self, attestations, **kw):
        return FakeCosign(Path(self.dir.name), attestations, **kw).path

    def test_a_properly_published_model_image_passes_and_the_log_is_ignored(self):
        cosign = self.cosign({"slsaprovenance1": [envelope(PREDICATE_TYPE, provenance("model"))],
                              "cyclonedx": [envelope(BOM_TYPE, {"metadata": {"component": {"type": "machine-learning-model"}}})]})
        result = verify_image(MODEL, self.key, "model", model="ticket-router", version="4", plain_http=True, cosign=cosign)
        self.assertEqual((result["signature"], result["provenance"], result["ml_bom"], result["version"]),
                         (True, True, True, "4"))
        self.assertEqual(result["source"], "c" * 40)
        calls = next(Path(self.dir.name).glob("*/calls.log")).read_text()
        self.assertIn("--insecure-ignore-tlog=true", calls)
        self.assertIn("--allow-http-registry", calls)
        self.assertEqual(calls.count("verify-attestation"), 2)

    def test_a_server_image_needs_provenance_but_no_ml_bom(self):
        cosign = self.cosign({"slsaprovenance1": [envelope(PREDICATE_TYPE, provenance("server"))]})
        result = verify_image(SERVER, self.key, "server", cosign=cosign)
        self.assertNotIn("ml_bom", result)
        self.assertEqual(next(Path(self.dir.name).glob("*/calls.log")).read_text().count("verify-attestation"), 1)

    def test_everything_that_admission_would_reject_is_refused_here(self):
        good_bom = envelope(BOM_TYPE, {"metadata": {"component": {"type": "machine-learning-model"}}})
        cases = [
            ("no signature", self.cosign({}, fail_verify=True), "verify failed"),
            ("no provenance", self.cosign({"slsaprovenance1": []}), "no SLSA provenance"),
            ("someone else's build", self.cosign({"slsaprovenance1": [envelope(PREDICATE_TYPE, provenance("model", build_type="https://example.com/other@v1"))]}), "not produced by this repository"),
            ("a server image passed off as the model", self.cosign({"slsaprovenance1": [envelope(PREDICATE_TYPE, provenance("server"))]}), "says this is a 'server' image"),
            ("another model's provenance", self.cosign({"slsaprovenance1": [envelope(PREDICATE_TYPE, provenance("model", version="3"))], "cyclonedx": [good_bom]}), "expected ticket-router v4"),
            ("no ML-BOM", self.cosign({"slsaprovenance1": [envelope(PREDICATE_TYPE, provenance("model"))], "cyclonedx": []}), "no ML-BOM attestation"),
        ]
        for name, cosign, message in cases:
            with self.subTest(case=name), self.assertRaisesRegex(ImageError, message):
                verify_image(MODEL, self.key, "model", model="ticket-router", version="4", cosign=cosign)
        with self.assertRaisesRegex(ImageError, "by digest"):
            verify_image("kind-registry:5000/ticket-router:v4", self.key, "model", cosign=self.cosign({}))
        with self.assertRaisesRegex(ImageError, "cosign is needed"):
            verify_image(MODEL, self.key, "model", cosign=str(Path(self.dir.name, "missing")))


if __name__ == "__main__":
    unittest.main()
