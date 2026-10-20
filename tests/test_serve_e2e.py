"""The whole local path, as the cluster runs it: train signed -> package -> serve from the bundle -> predict."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mlflow
import pandas as pd
from fastapi.testclient import TestClient

from secure_mlops.config import TrainConfig
from secure_mlops.data import DataSpec, generate
from secure_mlops.dataset import split, write
from secure_mlops.gate import GatePolicy
from secure_mlops.serve.app import create_app
from secure_mlops.serve.bundle import BundleError, load_verified
from secure_mlops.serve.package import package
from secure_mlops.signing import generate_keys
from secure_mlops.steps import train_and_gate

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
ROOT = Path(__file__).resolve().parents[1]


class ServeEndToEndTests(unittest.TestCase):
    def test_trained_signed_packaged_model_serves_and_a_tampered_bundle_never_does(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.dict(os.environ, {"SMLOPS_RISK_DIR": str(ROOT / "configs/models")}):
            root = Path(d)
            mlflow.set_tracking_uri(f"sqlite:///{root}/mlflow.db")
            try:
                write(split(pd.DataFrame(generate(DataSpec(n=3000)))), root / "data")
                private, public = generate_keys(root / "keys")
                outcome = train_and_gate(root / "data", TrainConfig(), GatePolicy(), "ticket-router", root / "out",
                                         private, public)
                bundle = package("ticket-router", public, root / "bundle")
            finally:
                mlflow.set_tracking_uri("")
            served = load_verified(bundle, public)
            self.assertAlmostEqual(sum(served.reference["teams"].values()), 1.0, places=4)   # profile travels, signed
            self.assertEqual(set(served.reference["teams"]), {"billing", "technical", "account", "shipping", "security"})
            from secure_mlops.serve.auth import digest
            client = TestClient(create_app(served, {digest("smk_e2e"): "e2e"}), headers={"Authorization": "Bearer smk_e2e"})
            r = client.post("/v1/models/ticket-router:predict", json={"instances": [
                {"text": "please refund the $40 charge from last week"},
                {"text": "someone logged into my account from another country"}]})
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertEqual([p["team"] for p in body["predictions"]], ["billing", "security"])
            self.assertEqual(body["model"]["version"], outcome.version)
            self.assertEqual(json.loads((bundle / "release.json").read_text())["run_id"], outcome.run_id)
            stored = bundle / "model" / "model.skops"
            data = bytearray(stored.read_bytes())
            data[len(data) // 3] ^= 0xFF
            stored.write_bytes(bytes(data))
            with self.assertRaisesRegex(BundleError, "refusing to serve"):
                load_verified(bundle, public)
