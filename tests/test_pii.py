import unittest

from secure_mlops.data import DataSpec, generate
from secure_mlops.pii import scan, summary


class PiiTests(unittest.TestCase):
    def test_finds_and_masks_common_identifiers(self):
        text = ("please call me on +91 98290 12345 or (415) 555-0134, mail jane.doe@example.com, "
                "card 4111 1111 1111 1111, IBAN GB82 WEST 1234 5698 7654 32")
        kinds = sorted(f.kind for f in scan(text))
        self.assertEqual(kinds, ["IBAN", "email", "payment card", "phone", "phone"])
        for f in scan(text):
            self.assertIn("***", f.masked)
            self.assertNotIn("98290 12345", f.masked)
            self.assertNotIn("jane.doe", f.masked)

    def test_checksums_keep_ordinary_numbers_out(self):
        for text in ("order #12345 ref 482913", "error E404 at 5pm", "case 123456789012", "4111 1111 1111 1112",
                     "invoice $229 for order #99999", "GB82 WEST 1234 5698 7654 33"):
            with self.subTest(text=text):
                self.assertEqual(scan(text), [])

    def test_synthetic_tickets_contain_no_personal_data(self):
        rows, kinds, _ = summary(r["text"] for r in generate(DataSpec(n=10000, drift=0.5)))
        self.assertEqual((rows, dict(kinds)), (0, {}))

    def test_names_where_tickets_carry_them_are_found_and_masked(self):
        for text, name in (("Hi, my name is Priya Shah and my order is late", "Priya Shah"),
                           ("this is Tom, the login keeps failing", "Tom"),
                           ("Dear Ms Okafor, the refund never came", "Okafor"),
                           ("parcel lost. Regards, Jan de Vries", "Jan de Vries"),
                           ("thanks, Sean O'Brien", "Sean O'Brien"),
                           ("I'm Jean-Luc Picard and I was charged twice", "Jean-Luc Picard")):
            with self.subTest(text=text):
                (finding,) = scan(text)
                self.assertEqual(finding.kind, "name")
                self.assertTrue(finding.masked.startswith(name[0]) and finding.masked.endswith(name[-1]))
                self.assertNotIn(name, finding.masked)

    def test_cue_words_without_a_person_are_not_names(self):
        for text in ("this is Urgent, the app crashes", "Thanks, Support Team", "Hi All, the site is down",
                     "I am Unable to log in", "this is the third time", "Hello there", "Regards, Billing",
                     "I am not getting emails", "Thanks for the help"):
            with self.subTest(text=text):
                self.assertEqual(scan(text), [])
