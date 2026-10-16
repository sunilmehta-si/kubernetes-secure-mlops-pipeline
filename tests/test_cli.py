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
