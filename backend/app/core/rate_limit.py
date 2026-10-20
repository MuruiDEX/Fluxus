"""In-memory login throttle (single-process desktop mode).

Per-identifier sliding window: at most `max_attempts` login attempts per
`window_seconds`. Both successes and failures count — simplicity beats
precision here, and it cannot weaken the guarantee.

Explicitly NOT durable: a process restart clears counters. Documented
limitation for future SaaS (move to shared store then). Thread-safety:
single event loop + short critical sections; prune-on-write bounds memory.
"""

import time
from collections import deque


class LoginThrottle:
    def __init__(
        self, max_attempts: int = 5, window_seconds: int = 300, max_keys: int = 10000
    ) -> None:
        self._max_attempts = max(1, max_attempts)
        self._window = max(1, window_seconds)
        # Bound for distinct identifiers: login attempts are keyed by email,
        # so an attacker spraying addresses must not grow memory without
        # limit. Oldest-inserted keys are evicted first (per-key windows are
        # unaffected — only long-untouched identifiers lose their history).
        self._max_keys = max(1, max_keys)
        self._hits: dict[str, deque[float]] = {}

    def _prune(self, key: str, now: float) -> deque[float]:
        hits = self._hits.get(key)
        if hits is None:
            return deque()
        cutoff = now - self._window
        while hits and hits[0] <= cutoff:
            hits.popleft()
        return hits

    def check(self, key: str) -> bool:
        """True if another attempt is allowed right now (does not record)."""
        return len(self._prune(key, time.monotonic())) < self._max_attempts

    def record(self, key: str) -> None:
        now = time.monotonic()
        if key not in self._hits:
            while len(self._hits) >= self._max_keys:
                # dicts preserve insertion order: evict the stalest key.
                self._hits.pop(next(iter(self._hits)))
            self._hits[key] = deque()
        self._prune(key, now).append(now)
