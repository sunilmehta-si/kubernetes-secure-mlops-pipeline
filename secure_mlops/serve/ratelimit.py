"""Per-client rate limits (threat T9): flooding the endpoint, or querying it fast enough to copy the model, stops.

A token bucket per client id, measured in tickets rather than requests, so one request of 64 tickets costs 64. A
client may burst up to `burst` tickets, then gets `per_minute` more each minute. Over the limit, the caller learns
how long to wait. Thread-safe; the clock is injectable so tests do not sleep.
"""
from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field


@dataclass
class TicketLimiter:
    per_minute: float = 600.0
    burst: float = 120.0
    clock: callable = time.monotonic
    _buckets: dict = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self):
        if self.per_minute <= 0 or self.burst <= 0:
            raise ValueError("per_minute and burst must be > 0")

    def take(self, client: str, tickets: int) -> tuple[bool, int]:
        """(allowed, seconds to wait before retrying). A request larger than the burst can never succeed."""
        if tickets > self.burst:
            return False, -1
        rate = self.per_minute / 60.0
        with self._lock:
            now = self.clock()
            level, last = self._buckets.get(client, (self.burst, now))
            level = min(self.burst, level + (now - last) * rate)
            if level >= tickets:
                self._buckets[client] = (level - tickets, now)
                return True, 0
            self._buckets[client] = (level, now)
            return False, max(1, math.ceil((tickets - level) / rate))
