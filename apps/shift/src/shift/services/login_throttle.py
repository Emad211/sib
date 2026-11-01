"""A sliding-window limit on login attempts per mobile (S-2).

Every login attempt costs one Argon2id verification (64 MB of memory), and the clinic computer is also the
machine the shift handover depends on. The account lock after five wrong PINs already protects one
person's PIN; this limit protects the machine from a flood of attempts, and it is per mobile on purpose:
behind the relay every visitor shares one address, so a per-address limit would let a stranger lock
everybody out. A throttled mobile is free again a minute later, unlike a locked account.
"""

import threading
from collections import defaultdict, deque
from datetime import timedelta

from shift.common.clock import Clock, SystemClock

ATTEMPTS = 12
WINDOW = timedelta(seconds=60)


class LoginThrottle:
    """Counts attempts per key in the last window; the caller refuses when `hit` says no."""

    def __init__(self, *, clock: Clock | None = None, attempts: int = ATTEMPTS, window: timedelta = WINDOW) -> None:
        self._clock = clock or SystemClock()
        self._attempts = attempts
        self._window = window
        self._seen: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str) -> bool:
        """Record one attempt for `key`; False when it already used up the window."""
        now = self._clock.now_utc().timestamp()
        floor = now - self._window.total_seconds()
        with self._lock:
            stamps = self._seen[key]
            while stamps and stamps[0] <= floor:
                stamps.popleft()
            if len(stamps) >= self._attempts:
                return False
            stamps.append(now)
            if len(self._seen) > 4096:  # a flood of invented mobiles must not grow memory without bound
                self._forget_idle(floor)
            return True

    def _forget_idle(self, floor: float) -> None:
        for key in [k for k, stamps in self._seen.items() if not stamps or stamps[-1] <= floor]:
            del self._seen[key]
