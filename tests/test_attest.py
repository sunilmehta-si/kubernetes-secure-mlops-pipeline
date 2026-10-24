import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from secure_mlops.attest import BUILD_TYPE, ProvenanceError, predicate

COMMIT = "a" * 40
NOW = dt.datetime(2026, 10, 23, 9, 0, tzinfo=dt.timezone.utc)
ACTIONS = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": "sunilmehta-si/kubernetes-secure-mlops-pipeline",
           "GITHUB_RUN_ID": "42", "GITHUB_RUN_ATTEMPT": "2", "GITHUB_SERVER_URL": "https://github.com",
           "GITHUB_WORKFLOW_REF": "sunilmehta-si/kubernetes-secure-mlops-pipeline/.github/workflows/cluster.yml@refs/heads/main"}


def bundle(d: str, datasets=("train", "test")) -> Path:
    root = Path(d)
    (root / "release.json").write_text(json.dumps({"name": "ticket-router", "version": "4", "run_id": "abc123"}))
    (root / "model.sig.json").write_text('{"signature": "x"}')
    (root / "mlbom.cdx.json").write_text(json.dumps({"components": [
        {"type": "data", "name": f"tickets-{s}", "hashes": [{"alg": "SHA-256", "content": s[0] * 64}]}
        for s in datasets] + [{"type": "library", "name": "pandas"}]}))
    return root


class ProvenanceTests(unittest.TestCase):
    def test_the_model_image_names_its_source_model_run_signature_and_data(self):
        with tempfile.TemporaryDirectory() as d:
            p = predicate("model", COMMIT, bundle(d), env=ACTIONS, now=NOW)
        build = p["buildDefinition"]
        self.assertEqual(build["buildType"], BUILD_TYPE)
        self.assertEqual(build["externalParameters"], {"kind": "model", "model": "ticket-router", "version": "4"})
        self.assertEqual(build["internalParameters"]["trainingRun"], "abc123")
        self.assertRegex(build["internalParameters"]["modelSignatureSha256"], r"^[0-9a-f]{64}$")
        self.assertEqual([r["uri"] for r in build["resolvedDependencies"]],
                         [f"git+https://github.com/sunilmehta-si/kubernetes-secure-mlops-pipeline@{COMMIT}",
                          "smlops:dataset/tickets-train", "smlops:dataset/tickets-test"])
        run = p["runDetails"]
        self.assertEqual(run["builder"]["id"], "https://github.com/sunilmehta-si/kubernetes-secure-mlops-pipeline"
                                               "/.github/workflows/cluster.yml")
        self.assertTrue(run["metadata"]["invocationId"].endswith("/actions/runs/42/attempts/2"))

    def test_the_server_image_names_its_source_and_dependency_lock(self):
        p = predicate("server", COMMIT, env={}, now=NOW)
        (source, lock) = p["buildDefinition"]["resolvedDependencies"]
        self.assertEqual(source["digest"], {"gitCommit": COMMIT})
        self.assertEqual(lock["uri"], "smlops:uv.lock")
        self.assertEqual(p["runDetails"], {"builder": {"id": "local"},
                                           "metadata": {"startedOn": "2026-10-23T09:00:00+00:00"}})

    def test_incomplete_provenance_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            for args, message in ((("model", "abc123", bundle(d)), "full git SHA"),
                                  (("model", COMMIT), "needs its bundle"),
                                  (("model", COMMIT, bundle(d, datasets=())), "names no datasets"),
                                  (("library", COMMIT), "model or server")):
                with self.subTest(message=message), self.assertRaisesRegex(ProvenanceError, message):
                    predicate(*args, env={})


if __name__ == "__main__":
    unittest.main()
