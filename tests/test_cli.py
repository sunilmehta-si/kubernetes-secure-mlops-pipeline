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
            self.assertEqual(main(["data", "generate", "--n", "400", "-o", d]), 0)
            manifest = json.loads((Path(d) / "manifest.json").read_text())
            rows = manifest["rows"]
            self.assertEqual((sum(rows.values()), rows["test"]), (400, 60))      # validation rounds to 60 or 61
            self.assertEqual(set(manifest["fingerprints"]), {"train", "validation", "test"})
            self.assertEqual(main(["data", "validate", str(Path(d) / "train.csv")]), 0)
            bad = Path(d) / "bad.csv"
            bad.write_text((Path(d) / "test.csv").read_text().replace(",email,", ",fax,", 1))
            self.assertEqual(main(["data", "validate", str(bad)]), 1)
            self.assertIn("'fax'", err.getvalue())
            self.assertEqual(main(["data", "generate", "--drift", "2", "-o", d]), 2)
