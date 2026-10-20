import tempfile
import unittest
from pathlib import Path

from secure_mlops.serve.auth import KeyFileError, add_client, authenticate, digest, load


class AuthTests(unittest.TestCase):
    def test_keys_are_stored_only_as_hashes_and_map_to_clients(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "keys.txt")
            support = add_client(path, "support-tool")
            ops = add_client(path, "ops")
            text = path.read_text()
            self.assertNotIn(support, text)
            self.assertIn(f"support-tool:{digest(support)}", text)
            entries = load(path)
            self.assertEqual((authenticate(entries, support), authenticate(entries, ops)), ("support-tool", "ops"))
            for bad in (None, "", "smk_wrong", support + "x"):
                self.assertIsNone(authenticate(entries, bad))
            with self.assertRaisesRegex(KeyFileError, "already has a key"):
                add_client(path, "ops")

    def test_malformed_empty_and_duplicate_files_are_errors(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "keys.txt")
            for text, message in (("# only a comment\n", "no keys"), ("Support:" + "a" * 64 + "\n", "expected client-id"),
                                  ("a:" + "b" * 63 + "\n", "expected client-id"),
                                  ("a:" + "c" * 64 + "\nb:" + "c" * 64 + "\n", "duplicate key")):
                path.write_text(text)
                with self.subTest(text=text[:20]), self.assertRaisesRegex(KeyFileError, message):
                    load(path)
            with self.assertRaisesRegex(KeyFileError, "lowercase"):
                add_client(path, "Bad Client")
