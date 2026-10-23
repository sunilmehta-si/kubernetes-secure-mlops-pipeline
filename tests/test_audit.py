import json
import unittest

from secure_mlops.audit import markdown, summarize


def event(kind, client, tickets, **extra):
    return json.dumps({"event": kind, "client": client, "tickets": tickets, **extra})


LINES = [event("predict", "support-tool", 2, digest="sha256:aa"),
         event("predict", "support-tool", 3, digest="sha256:aa"),
         event("predict", "chat-bot", 1, digest="sha256:bb"),
         event("feedback", "support-tool", 5, correct=4),
         event("feedback", "chat-bot", 1, correct=0),
         "2026-10-23T09:00:00Z INFO " + event("predict", "chat-bot", 1, digest="sha256:bb"),
         "uvicorn started", "", '{"event": "predict"}']


class AuditSummaryTests(unittest.TestCase):
    def test_tickets_and_confirmed_accuracy_per_client_and_requests_per_model(self):
        s = summarize(LINES)
        self.assertEqual(s["clients"]["support-tool"],
                         {"requests": 2, "tickets": 5, "confirmed": 5, "correct": 4, "accuracy": 0.8})
        self.assertEqual(s["clients"]["chat-bot"]["accuracy"], 0.0)
        self.assertEqual(s["clients"]["chat-bot"]["requests"], 2)              # the prefixed line counts too
        self.assertEqual(s["models"], {"sha256:aa": 2, "sha256:bb": 2})
        self.assertEqual(s["skipped"], 2)                                      # not JSON, and incomplete

    def test_markdown_tables(self):
        text = markdown(summarize(LINES))
        self.assertIn("| support-tool | 2 | 5 | 5 | 4 (80.0%) |", text)
        self.assertIn("| `sha256:aa` | 2 |", text)
        self.assertIn("2 line(s) were not audit events", text)
        self.assertIn("| nobody | 0 | 0 | 0 | - |", markdown({"clients": {"nobody": {
            "requests": 0, "tickets": 0, "confirmed": 0, "correct": 0, "accuracy": None}}, "models": {}, "skipped": 0}))


if __name__ == "__main__":
    unittest.main()
