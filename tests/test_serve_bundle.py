import json
import os
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from secure_mlops.config import TrainConfig
from secure_mlops.data import DataSpec, generate
from secure_mlops.dataset import split
from secure_mlops.serve.bundle import BundleError, load_verified
from secure_mlops.signing import generate_keys, sign
from secure_mlops.steps import save_model
from secure_mlops.train import train

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
MODEL = train(split(pd.DataFrame(generate(DataSpec(n=1000)))).train, TrainConfig())


def make_bundle(root: Path, keys: Path) -> tuple[Path, Path]:
    private, public = generate_keys(keys)
    save_model(MODEL, root / "model")
    sign(root / "model", root / "model.sig.json", private)
    (root / "release.json").write_text(json.dumps({"name": "ticket-router", "version": "4", "run_id": "abc"}))
    return private, public


class BundleTests(unittest.TestCase):
    def test_verified_bundle_loads_with_its_identity(self):
        with tempfile.TemporaryDirectory() as d:
            _, public = make_bundle(Path(d, "b"), Path(d, "keys"))
            served = load_verified(Path(d, "b"), public)
            self.assertEqual((served.name, served.version), ("ticket-router", "4"))
            self.assertRegex(served.digest, r"^sha256:[0-9a-f]{64}$")
            x = pd.DataFrame({"text": ["please refund the $40 charge from last week"]})
            self.assertEqual(list(served.predict(x)), ["billing"])

    def test_tampered_incomplete_or_wrongly_keyed_bundles_never_load(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d, "b")
            _, public = make_bundle(root, Path(d, "keys"))
            _, other = generate_keys(Path(d, "other"))
            with self.assertRaisesRegex(BundleError, "refusing to serve"):
                load_verified(root, other)
            target = root / "model" / "model.skops"
            data = bytearray(target.read_bytes())
            data[len(data) // 2] ^= 0xFF
            target.write_bytes(bytes(data))
            with self.assertRaisesRegex(BundleError, "refusing to serve"):
                load_verified(root, public)
            (root / "model.sig.json").unlink()
            with self.assertRaisesRegex(BundleError, "missing model.sig.json"):
                load_verified(root, public)
