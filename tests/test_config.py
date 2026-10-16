import tempfile
import unittest
from pathlib import Path

from secure_mlops.config import ConfigError, TrainConfig, load

ROOT = Path(__file__).resolve().parents[1]


class ConfigTests(unittest.TestCase):
    def test_committed_config_loads_and_matches_defaults(self):
        self.assertEqual(load(ROOT / "configs/train.yaml"), TrainConfig())

    def test_hash_identifies_parameters(self):
        self.assertEqual(TrainConfig().params_hash(), TrainConfig().params_hash())
        self.assertNotEqual(TrainConfig().params_hash(), TrainConfig(c=1.0).params_hash())

    def test_unknown_keys_and_bad_values_are_errors(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.yaml"
            p.write_text("max_featurs: 10\n")
            with self.assertRaisesRegex(ConfigError, "unknown key\\(s\\) max_featurs"):
                load(p)
            p.write_text("- 1\n")
            with self.assertRaisesRegex(ConfigError, "expected a mapping"):
                load(p)
        for bad in ({"c": 0}, {"ngram_max": 0}, {"class_weight": "heavy"}):
            with self.assertRaises(ConfigError):
                TrainConfig(**bad)
