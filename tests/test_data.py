import unittest
from collections import Counter

from secure_mlops.data import LABELS, NEW_PRODUCTS, DataSpec, generate


class GeneratorTests(unittest.TestCase):
    def test_same_spec_same_tickets_and_every_label_present(self):
        a, b = generate(DataSpec(n=500, seed=3)), generate(DataSpec(n=500, seed=3))
        self.assertEqual(a, b)
        self.assertNotEqual(a, generate(DataSpec(n=500, seed=4)))
        self.assertEqual(set(r["label"] for r in a), set(LABELS))
        self.assertEqual(len({r["ticket_id"] for r in a}), 500)

    def test_classes_are_roughly_balanced_without_drift(self):
        counts = Counter(r["label"] for r in generate(DataSpec(n=5000)))
        self.assertTrue(all(0.15 < c / 5000 < 0.25 for c in counts.values()), counts)

    def test_drift_shifts_vocabulary_channels_and_class_mix(self):
        base, shifted = generate(DataSpec(n=4000)), generate(DataSpec(n=4000, drift=1.0))
        new_words = lambda rows: sum(any(p in r["text"] for p in NEW_PRODUCTS) for r in rows)   # noqa: E731
        self.assertEqual(new_words(base), 0)
        self.assertGreater(new_words(shifted), 400)
        share = lambda rows, key, value: sum(r[key] == value for r in rows) / len(rows)          # noqa: E731
        self.assertGreater(share(shifted, "channel", "chat"), share(base, "channel", "chat") + 0.25)
        self.assertGreater(share(shifted, "label", "technical"), 0.3)

    def test_duplicate_text_stays_under_the_contract_limit_at_scale(self):
        from secure_mlops.validate import MAX_DUPLICATE_TEXT_SHARE
        texts = [r["text"] for r in generate(DataSpec(n=10000))]
        self.assertLess(1 - len(set(texts)) / len(texts), MAX_DUPLICATE_TEXT_SHARE)

    def test_spec_is_validated(self):
        for bad in ({"n": 2}, {"label_noise": 1.5}, {"drift": -0.1}):
            with self.assertRaises(ValueError):
                DataSpec(**bad)
