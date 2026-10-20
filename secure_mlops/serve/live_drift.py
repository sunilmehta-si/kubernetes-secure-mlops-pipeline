"""Live drift on the serving path (gate G7, threat T10): is the traffic the model sees right now still like the
traffic it was trained on?

A sliding window of recent tickets, compared with the model's own signed reference profile:
- PSI of the predicted teams against the training team mix (what the model is deciding);
- the share of words its own vectorizer has never seen (new products, new phrasing), as a ratio to the model's own
  rate on held-out tickets, since the vectorizer's dropped rare words give even training-like text a baseline.
Nothing is reported until the window holds `min_tickets`, so a quiet minute does not look like drift. The same PSI
and thresholds as `smlops drift` apply (configs/drift.yaml), so batch and live checks agree.
"""
from __future__ import annotations

import re
import threading
from collections import Counter, deque

from ..drift import psi

WORD = re.compile(r"[a-z]+")


def model_vocabulary(model) -> frozenset[str]:
    """The single words the model's TF-IDF vectorizer knows (its n-gram keys contain spaces)."""
    for _, step in getattr(model, "steps", []):
        for _, transformer, _ in getattr(step, "transformers_", []):
            vocab = getattr(transformer, "vocabulary_", None)
            if vocab:
                return frozenset(k for k in vocab if " " not in k)
    return frozenset()


def new_word_rate(texts, vocabulary: frozenset[str]) -> float:
    words = [w for t in texts for w in WORD.findall(t.lower())]
    return sum(w not in vocabulary for w in words) / len(words) if words else 0.0


class LiveDrift:
    def __init__(self, reference_teams: dict[str, float] | None, vocabulary: frozenset[str], window: int = 500,
                 min_tickets: int = 200, baseline_new_word_rate: float | None = None):
        if window < min_tickets or min_tickets < 1:
            raise ValueError("window must hold at least min_tickets, and min_tickets must be at least 1")
        self.reference = Counter({team: share * 10_000 for team, share in (reference_teams or {}).items()})
        self.vocabulary = vocabulary
        self.min_tickets = min_tickets
        self.baseline = baseline_new_word_rate
        self._recent: deque[tuple[str, int, int]] = deque(maxlen=window)    # (team, new words, words)
        self._lock = threading.Lock()

    def observe(self, texts, teams) -> None:
        rows = []
        for text, team in zip(texts, teams):
            words = WORD.findall(text.lower())
            rows.append((str(team), sum(w not in self.vocabulary for w in words), len(words)))
        with self._lock:
            self._recent.extend(rows)

    def snapshot(self) -> dict:
        """{tickets, psi_predicted_team, new_word_rate, new_word_ratio}; all but tickets are None until min_tickets
        have been seen; the ratio needs the model's baseline."""
        with self._lock:
            rows = list(self._recent)
        out = {"tickets": len(rows), "psi_predicted_team": None, "new_word_rate": None, "new_word_ratio": None}
        if len(rows) < self.min_tickets:
            return out
        if self.reference:
            out["psi_predicted_team"] = round(psi(self.reference, Counter(team for team, _, _ in rows)), 4)
        words = sum(n for _, _, n in rows)
        out["new_word_rate"] = round(sum(new for _, new, _ in rows) / words, 4) if words else 0.0
        if self.baseline:
            out["new_word_ratio"] = round(out["new_word_rate"] / self.baseline, 3)
        return out
