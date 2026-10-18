import json
import tempfile
import unittest
from pathlib import Path

from secure_mlops.mlbom import build, file_hashes, summary
from secure_mlops.provenance import frozen_requirements


class MlBomTests(unittest.TestCase):
    def test_valid_cyclonedx_with_model_files_data_libraries_and_facts(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "model.skops").write_bytes(b"weights")
            Path(d, "MLmodel").write_text("flavors: {}\n")
            text = build("ticket-router", "3", file_hashes(d), {"train": "sha256:" + "a" * 64, "test": "sha256:" + "b" * 64},
                         frozen_requirements(), {"gate": "passed", "params_hash": "sha256:" + "c" * 64})
        doc = json.loads(text)
        self.assertEqual((doc["bomFormat"], doc["specVersion"]), ("CycloneDX", "1.6"))
        model = doc["metadata"]["component"]
        self.assertEqual((model["type"], model["name"], model["version"]), ("machine-learning-model", "ticket-router", "3"))
        self.assertIn({"name": "smlops:gate", "value": "passed"}, model["properties"])
        files = {c["name"]: c["hashes"][0]["content"] for c in model["components"]}
        import hashlib
        self.assertEqual(files["model.skops"], hashlib.sha256(b"weights").hexdigest())
        s = summary(text)
        self.assertEqual((s["datasets"], s["model_files"]), (2, 2))
        self.assertGreater(s["libraries"], 20)
        libs = {c["name"]: c for c in doc["components"] if c["type"] == "library"}
        self.assertTrue(libs["scikit-learn"]["purl"].startswith("pkg:pypi/scikit-learn@"))
        model_deps = next(d for d in doc["dependencies"] if d["ref"] == model["bom-ref"])["dependsOn"]
        self.assertIn("data:train", model_deps)
