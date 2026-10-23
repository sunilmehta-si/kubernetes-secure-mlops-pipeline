"""Feedback from the people who handle tickets: did the model pick the right team? (gate G7, threat T10)

Drift says traffic changed; only labels say whether the model got worse. Support agents see which team really owns
each ticket, and the support tool sends that back for the prediction it made, by request id. The server keeps:

- a bounded log of recent predictions (request id -> who asked, which teams were predicted), so feedback can only
  confirm a prediction this server actually made, only once, and only from the client that asked for it: a client
  cannot pad the accuracy of tickets it never sent, or count a ticket twice;
- a sliding window of (predicted, confirmed) pairs, reporting accuracy and its drop below the model's own held-out
  accuracy, which is signed into the model's reference profile. Nothing is reported until the window holds
  `min_labels`, so a handful of corrections is not an outage.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass


class FeedbackError(ValueError):
    """Feedback that cannot be counted; `status` is the HTTP status to answer with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class _Logged:
    client: str
    teams: list[str]
    at: float
    confirmed: bool = False


class PredictionLog:
    def __init__(self, capacity: int = 10_000, max_age_seconds: float = 7 * 24 * 3600,
                 clock: Callable[[], float] = time.monotonic):
        self.capacity, self.max_age, self.clock = capacity, max_age_seconds, clock
        self._entries: OrderedDict[str, _Logged] = OrderedDict()
        self._lock = threading.Lock()

    def record(self, request_id: str, client: str, teams: list[str]) -> None:
        with self._lock:
            self._entries[request_id] = _Logged(client, list(teams), self.clock())
            self._entries.move_to_end(request_id)
            while len(self._entries) > self.capacity:
                self._entries.popitem(last=False)

    def confirm(self, request_id: str, client: str, confirmed: list[str]) -> list[tuple[str, str]]:
        """(predicted, confirmed) pairs for a logged request, once; FeedbackError otherwise."""
        with self._lock:
            entry = self._entries.get(request_id)
            if entry is None or self.clock() - entry.at > self.max_age:
                raise FeedbackError(404, "no recent prediction with that request id")
            if entry.client != client:
                raise FeedbackError(403, "feedback must come from the client that asked for the prediction")
            if entry.confirmed:
                raise FeedbackError(409, "feedback for this request was already counted")
            if len(confirmed) != len(entry.teams):
                raise FeedbackError(422, f"the request had {len(entry.teams)} tickets; send one team for each")
            entry.confirmed = True
            return list(zip(entry.teams, confirmed))


class LiveAccuracy:
    def __init__(self, baseline: float | None, window: int = 500, min_labels: int = 100):
        if window < min_labels or min_labels < 1:
            raise ValueError("window must hold at least min_labels, and min_labels must be at least 1")
        self.baseline, self.min_labels = baseline, min_labels
        self._pairs: deque[tuple[str, str]] = deque(maxlen=window)
        self._lock = threading.Lock()

    def observe(self, pairs: list[tuple[str, str]]) -> None:
        with self._lock:
            self._pairs.extend(pairs)

    def snapshot(self) -> dict:
        """{labelled, accuracy, accuracy_drop}; the last two are None below min_labels, the drop without a baseline."""
        with self._lock:
            pairs = list(self._pairs)
        out = {"labelled": len(pairs), "accuracy": None, "accuracy_drop": None}
        if len(pairs) < self.min_labels:
            return out
        out["accuracy"] = round(sum(p == c for p, c in pairs) / len(pairs), 4)
        if self.baseline is not None:
            out["accuracy_drop"] = round(self.baseline - out["accuracy"], 4)
        return out
