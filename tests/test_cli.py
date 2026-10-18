import io
import unittest
from contextlib import redirect_stdout

from secure_mlops import __version__
from secure_mlops.cli import main


class CliTests(unittest.TestCase):
    def test_version(self):
        out = io.StringIO()
        with redirect_stdout(out), self.assertRaises(SystemExit):
            main(["--version"])
        self.assertIn(__version__, out.getvalue())

    def test_no_command_prints_help(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main([]), 0)
        self.assertIn("usage: smlops", out.getvalue())


class DataCliTests(unittest.TestCase):
    def test_generate_writes_splits_and_manifest_then_validate_passes(self):
        import json
        import tempfile
        from contextlib import redirect_stderr
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d, redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main(["data", "generate", "--n", "1000", "-o", d]), 0)   # 30 per team in each split
            manifest = json.loads((Path(d) / "manifest.json").read_text())
            rows = manifest["rows"]
            self.assertEqual((sum(rows.values()), rows["test"]), (1000, 150))
            self.assertEqual(set(manifest["fingerprints"]), {"train", "validation", "test"})
            self.assertEqual(main(["data", "validate", str(Path(d) / "train.csv")]), 0)
            bad = Path(d) / "bad.csv"
            bad.write_text((Path(d) / "test.csv").read_text().replace(",email,", ",fax,", 1))
            self.assertEqual(main(["data", "validate", str(bad)]), 1)
            self.assertIn("'fax'", err.getvalue())
            self.assertEqual(main(["data", "generate", "--drift", "2", "-o", d]), 2)

    def test_too_small_a_dataset_is_refused(self):
        import tempfile
        from contextlib import redirect_stderr
        with tempfile.TemporaryDirectory() as d, redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main(["data", "generate", "--n", "400", "-o", d]), 1)
        self.assertIn("validation split failed the data contract", err.getvalue())   # ~12 tickets per team

    def test_default_dataset_passes_its_own_contract(self):
        # regression: the default 3000 tickets used to repeat template text 28% of the time, over the 25% limit
        import tempfile
        from contextlib import redirect_stderr
        with tempfile.TemporaryDirectory() as d, redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main(["data", "generate", "-o", d]), 0, err.getvalue())


class TrainCliTests(unittest.TestCase):
    def test_train_promotes_then_blocks_a_worse_config(self):
        import tempfile
        from contextlib import redirect_stderr
        from pathlib import Path

        import mlflow
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as d, redirect_stderr(io.StringIO()) as err:
            try:
                self.assertEqual(main(["data", "generate", "--n", "3000", "-o", f"{d}/data"]), 0)
                common = ["train", "--data", f"{d}/data", "--tracking-uri", f"sqlite:///{d}/mlflow.db", "-o", f"{d}/out",
                          "--gate", str(root / "configs/gate.yaml")]
                out = io.StringIO()
                with redirect_stdout(out):
                    self.assertEqual(main([*common, "--config", str(root / "configs/train.yaml")]), 0)
                self.assertIn("**passed**", out.getvalue())
                self.assertIn("v1 promoted to champion", err.getvalue())
                weak = Path(d) / "weak.yaml"
                weak.write_text("c: 0.05\nmin_df: 5\n")
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(main([*common, "--config", str(weak)]), 1)
                self.assertIn("v2 registered, not promoted", err.getvalue())
                self.assertEqual(main([*common, "--config", f"{d}/missing.yaml"]), 2)
            finally:
                mlflow.set_tracking_uri("")


class SigningCliTests(unittest.TestCase):
    def test_keys_train_signed_and_verify(self):
        import tempfile
        from contextlib import redirect_stderr
        from pathlib import Path

        import mlflow
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as d, redirect_stderr(io.StringIO()) as err, redirect_stdout(io.StringIO()):
            uri = f"sqlite:///{d}/mlflow.db"
            try:
                self.assertEqual(main(["keys", "generate", "-o", f"{d}/keys"]), 0)
                self.assertEqual(main(["keys", "generate", "-o", f"{d}/keys"]), 1)        # never overwrites
                self.assertEqual(main(["data", "generate", "-o", f"{d}/data"]), 0)
                self.assertEqual(main(["train", "--data", f"{d}/data", "--tracking-uri", uri, "-o", f"{d}/out",
                                       "--config", str(root / "configs/train.yaml"), "--gate", str(root / "configs/gate.yaml"),
                                       "--signing-key", f"{d}/keys/signing-key.pem"]), 0)
                verify = ["verify", "--public-key", f"{d}/keys/signing-key.pub.pem", "--tracking-uri", uri]
                self.assertEqual(main(verify), 0)
                self.assertIn("v1 verified", err.getvalue())
                self.assertEqual(main([*verify, "--alias", "missing"]), 2)
            finally:
                mlflow.set_tracking_uri("")
