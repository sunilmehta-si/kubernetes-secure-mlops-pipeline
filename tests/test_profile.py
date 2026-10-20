import tempfile
import unittest
from pathlib import Path

import pandas as pd

from secure_mlops.profile import read_profile, reference_profile


class ProfileTests(unittest.TestCase):
    def test_team_shares_and_reading_from_mlmodel(self):
        frame = pd.DataFrame({"label": ["billing"] * 3 + ["security"]})
        profile = reference_profile(frame)
        self.assertEqual(profile, {"tickets": 4, "teams": {"billing": 0.75, "security": 0.25}})
        with tempfile.TemporaryDirectory() as d:
            self.assertIsNone(read_profile(d))
            Path(d, "MLmodel").write_text("flavors: {}\nmetadata:\n  smlops_reference:\n    tickets: 4\n")
            self.assertEqual(read_profile(d), {"tickets": 4})
