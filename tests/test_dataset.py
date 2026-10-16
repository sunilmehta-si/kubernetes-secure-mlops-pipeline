import tempfile
import unittest

import pandas as pd

from secure_mlops.data import DataSpec, generate
from secure_mlops.dataset import fingerprint, read, split, text_overlap, write

FRAME = pd.DataFrame(generate(DataSpec(n=1000)))


class SplitTests(unittest.TestCase):
    def test_disjoint_stratified_and_deterministic(self):
        s = split(FRAME)
        ids = [set(d["ticket_id"]) for _, d in s.items()]
        self.assertEqual(sum(len(i) for i in ids), 1000)
        self.assertFalse(ids[0] & ids[1] or ids[0] & ids[2] or ids[1] & ids[2])
        self.assertEqual((len(s.train), len(s.validation), len(s.test)), (700, 150, 150))
        share = lambda d: d["label"].value_counts(normalize=True)   # noqa: E731
        self.assertLess((share(s.test) - share(FRAME)).abs().max(), 0.02)
        self.assertTrue(split(FRAME).test.equals(s.test))
        self.assertFalse(split(FRAME, seed=8).test.equals(s.test))
        with self.assertRaises(ValueError):
            split(FRAME, validation=0.6, test=0.5)

    def test_fingerprint_identifies_content_not_order(self):
        shuffled = FRAME.sample(frac=1, random_state=1)[list(reversed(FRAME.columns))]
        self.assertEqual(fingerprint(shuffled), fingerprint(FRAME))
        changed = FRAME.copy()
        changed.loc[0, "text"] += "!"
        self.assertNotEqual(fingerprint(changed), fingerprint(FRAME))
        self.assertTrue(fingerprint(FRAME).startswith("sha256:"))

    def test_round_trip_keeps_fingerprints(self):
        s = split(FRAME)
        with tempfile.TemporaryDirectory() as d:
            prints = write(s, d)
            self.assertEqual(fingerprint(read(d, "test")), prints["test"])

    def test_overlap_is_measured(self):
        self.assertTrue(0.0 <= text_overlap(split(FRAME)) < 1.0)
