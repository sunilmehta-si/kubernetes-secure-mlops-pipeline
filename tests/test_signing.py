import stat
import tempfile
import unittest
from pathlib import Path

from secure_mlops.signing import SignatureError, generate_keys, sign, verify


class SigningTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = Path(self.dir.name)
        self.private, self.public = generate_keys(self.root / "keys")
        self.model = self.root / "model"
        self.model.mkdir()
        (self.model / "model.skops").write_bytes(b"weights" * 100)
        (self.model / "MLmodel").write_text("flavors: {}\n")

    def tearDown(self):
        self.dir.cleanup()

    def test_private_key_is_owner_only_and_never_overwritten(self):
        self.assertEqual(stat.S_IMODE(self.private.stat().st_mode), 0o600)
        with self.assertRaisesRegex(SignatureError, "refusing to overwrite"):
            generate_keys(self.root / "keys")

    def test_signed_model_verifies_and_any_change_breaks_it(self):
        sig = sign(self.model, self.root / "model.sig.json", self.private)
        verify(self.model, sig, self.public)
        (self.model / "model.skops").write_bytes(b"weights" * 99 + b"evil!!!")
        with self.assertRaisesRegex(SignatureError, "changed after signing"):
            verify(self.model, sig, self.public)

    def test_added_file_wrong_key_and_missing_signature_fail(self):
        sig = sign(self.model, self.root / "model.sig.json", self.private)
        (self.model / "extra.py").write_text("print('hi')")
        with self.assertRaises(SignatureError):
            verify(self.model, sig, self.public)
        (self.model / "extra.py").unlink()
        _, other = generate_keys(self.root / "other")
        with self.assertRaises(SignatureError):
            verify(self.model, sig, other)
        with self.assertRaisesRegex(SignatureError, "has no signature"):
            verify(self.model, self.root / "missing.json", self.public)
        with self.assertRaisesRegex(SignatureError, "outside the model directory"):
            sign(self.model, self.model / "sig.json", self.private)
