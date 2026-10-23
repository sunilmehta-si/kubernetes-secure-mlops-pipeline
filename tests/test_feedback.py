import unittest

from secure_mlops.serve.feedback import FeedbackError, LiveAccuracy, PredictionLog


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class PredictionLogTests(unittest.TestCase):
    def test_only_the_asking_client_confirms_each_prediction_once(self):
        log = PredictionLog()
        log.record("r1", "support-tool", ["billing", "shipping"])
        for request, client, teams, status in (("r9", "support-tool", ["billing", "billing"], 404),
                                                ("r1", "other-client", ["billing", "billing"], 403),
                                                ("r1", "support-tool", ["billing"], 422)):
            with self.subTest(status=status), self.assertRaises(FeedbackError) as caught:
                log.confirm(request, client, teams)
            self.assertEqual(caught.exception.status, status)
        self.assertEqual(log.confirm("r1", "support-tool", ["billing", "account"]),
                         [("billing", "billing"), ("shipping", "account")])
        with self.assertRaises(FeedbackError) as caught:
            log.confirm("r1", "support-tool", ["billing", "account"])
        self.assertEqual(caught.exception.status, 409)

    def test_old_and_overflowing_entries_are_forgotten(self):
        clock = Clock()
        log = PredictionLog(capacity=2, max_age_seconds=60, clock=clock)
        for rid in ("a", "b", "c"):
            log.record(rid, "c1", ["billing"])
        with self.assertRaises(FeedbackError):
            log.confirm("a", "c1", ["billing"])                               # pushed out by capacity
        clock.now = 61
        with self.assertRaises(FeedbackError):
            log.confirm("b", "c1", ["billing"])                               # too old


class LiveAccuracyTests(unittest.TestCase):
    def test_reports_accuracy_and_its_drop_once_the_window_has_enough_labels(self):
        live = LiveAccuracy(baseline=0.94, window=200, min_labels=100)
        live.observe([("billing", "billing")] * 50)
        self.assertEqual(live.snapshot(), {"labelled": 50, "accuracy": None, "accuracy_drop": None})
        live.observe([("billing", "billing")] * 30 + [("billing", "account")] * 20)
        self.assertEqual(live.snapshot(), {"labelled": 100, "accuracy": 0.8, "accuracy_drop": 0.14})

    def test_recall_per_team_shows_the_team_overall_accuracy_hides(self):
        live = LiveAccuracy(baseline=0.95, window=500, min_labels=100)
        live.observe([("shipping", "shipping")] * 180 + [("billing", "billing")] * 14 + [("shipping", "billing")] * 6
                     + [("account", "account")] * 5)
        self.assertEqual(live.snapshot()["accuracy"], 0.9707)                  # looks fine overall
        self.assertEqual(live.recall_by_team(), {"billing": 0.7, "shipping": 1.0})   # billing does not
        self.assertNotIn("account", live.recall_by_team())                    # five labels say nothing yet

    def test_the_window_slides_and_a_model_without_a_baseline_reports_no_drop(self):
        live = LiveAccuracy(baseline=None, window=100, min_labels=100)
        live.observe([("a", "b")] * 100 + [("a", "a")] * 100)
        self.assertEqual(live.snapshot(), {"labelled": 100, "accuracy": 1.0, "accuracy_drop": None})
        with self.assertRaises(ValueError):
            LiveAccuracy(0.9, window=10, min_labels=20)


if __name__ == "__main__":
    unittest.main()
