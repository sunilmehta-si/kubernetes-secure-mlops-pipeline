import unittest

import pandas as pd

from secure_mlops.data import DataSpec, generate
from secure_mlops.validate import validate


def frame(n=600, **spec):
    return pd.DataFrame(generate(DataSpec(n=n, **spec)))


class ValidateTests(unittest.TestCase):
    def test_generated_tickets_pass(self):
        r = validate(frame())
        self.assertTrue(r.ok, r.problems)
        self.assertEqual(r.rows, 600)

    def test_row_problems_are_all_reported_at_once(self):
        f = frame()
        f.loc[0, "channel"] = "fax"
        f.loc[1, "label"] = "sales"
        f.loc[2, "text"] = "hi"
        problems = validate(f).problems
        self.assertEqual(len(problems), 3, problems)
        self.assertTrue(any("channel" in p and "'fax'" in p for p in problems))
        self.assertTrue(any(p.startswith("label") for p in problems))
        self.assertTrue(any(p.startswith("text") for p in problems))

    def test_unexpected_or_missing_columns_fail(self):
        self.assertFalse(validate(frame().assign(extra=1)).ok)
        self.assertFalse(validate(frame().drop(columns=["tier"])).ok)

    def test_dataset_checks(self):
        f = frame()
        lopsided = pd.concat([f[f["label"] != "security"], f[f["label"] == "security"].head(5)])
        self.assertIn("only 5 'security' tickets", " ".join(validate(lopsided).problems))
        dup = pd.concat([f] + [f.assign(ticket_id=f["ticket_id"] + f"-{k}") for k in range(2)])
        self.assertIn("duplicate another ticket", " ".join(validate(dup).problems))
