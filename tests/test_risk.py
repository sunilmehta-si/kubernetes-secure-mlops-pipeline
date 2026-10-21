import datetime as dt
import tempfile
import unittest
from pathlib import Path

import yaml

from secure_mlops.risk import RiskError, load, parse

ROOT = Path(__file__).resolve().parents[1]
ENTRY = yaml.safe_load((ROOT / "configs/models/ticket-router.yaml").read_text())


class RiskTests(unittest.TestCase):
    def test_committed_entry_is_valid(self):
        e = load("ticket-router", ROOT / "configs/models")
        self.assertEqual((e.risk_tier, e.human_in_the_loop), ("minimal", True))

    def test_missing_unknown_and_bad_values_are_errors(self):
        for change, message in (({"owner": ""}, "missing owner"), ({"extra": 1}, "unknown extra"),
                                ({"risk_tier": "low"}, "risk_tier must be"),
                                ({"risk_tier": "high", "human_in_the_loop": False}, "human in the loop"),
                                ({"review_by": dt.date(2020, 1, 1)}, "review_by after reviewed")):
            with self.subTest(change=change), self.assertRaisesRegex(RiskError, message):
                parse({**ENTRY, **change})

    def test_overdue_review_and_missing_entry_block_promotion(self):
        e = parse(ENTRY)
        e.check_current(e.review_by)
        with self.assertRaisesRegex(RiskError, "review was due"):
            e.check_current(e.review_by + dt.timedelta(days=1))
        with tempfile.TemporaryDirectory() as d, self.assertRaisesRegex(RiskError, "no risk register entry"):
            load("other-model", d)

    def test_a_retired_model_is_never_trained_or_promoted_again(self):
        e = parse({**ENTRY, "retired": dt.date(2026, 11, 1), "retired_reason": "replaced by a vendor router"})
        e.check_current(dt.date(2026, 10, 31))                          # still live the day before
        with self.assertRaisesRegex(RiskError, "retired on 2026-11-01 \\(replaced by a vendor router\\)"):
            e.check_current(dt.date(2026, 11, 1))
        for change in ({"retired": dt.date(2026, 11, 1)}, {"retired_reason": "why"},
                       {"retired": "soon", "retired_reason": "why"}):
            with self.subTest(change=change), self.assertRaisesRegex(RiskError, "retired"):
                parse({**ENTRY, **change})
