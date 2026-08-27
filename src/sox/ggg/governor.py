"""Rate governor for GGG endpoints.

Limits are advertised only on live responses, so this starts permissive and
tightens as soon as it sees headers. It sleeps BEFORE issuing a call that
would breach a rule rather than reacting to a 429, because a 429 already
costs a restriction window.

Real limits, captured live 2026-08-18 (search) and 2026-08-27 (fetch):

    search  x-rate-limit-policy: trade-search-request-limit
            x-rate-limit-rules:  Ip
            x-rate-limit-ip:     5:10:60,15:60:300,30:300:1800,600:21600:3600
    fetch   x-rate-limit-policy: trade-fetch-request-limit
            x-rate-limit-ip:     12:4:10,16:12:300,50:300:300,1000:21600:1800

One rule carries SEVERAL comma-separated clauses, all enforced at once.
Parsing only the first would breach the longer windows.

Each policy is its own budget — the -state counters prove it, a search's
count does not move across fetches — so a governor is one policy's pacing
and the session keeps one per endpoint. Its name is only for the wait line,
which otherwise cannot say which bucket is full.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass

BASE_BACKOFF = 5.0
MAX_BACKOFF = 300.0


@dataclass(frozen=True)
class Rule:
    name: str
    limit: int
    period: int
    restriction: int


class RateGovernor:
    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        on_wait: Callable[[float, str], None] | None = None,
        name: str = "",
    ) -> None:
        self._clock = clock
        self._sleep = sleeper
        # Waiting silently looks identical to hanging, so callers can show it.
        self._on_wait = on_wait or (lambda seconds, reason: None)
        self._name = name
        self.rules: list[Rule] = []
        self._history: deque[float] = deque()
        self._consecutive_429 = 0

    def observe(self, headers: Mapping[str, str]) -> None:
        names = headers.get("X-Rate-Limit-Rules") or headers.get("x-rate-limit-rules")
        if not names:
            return
        rules: list[Rule] = []
        for name in (n.strip() for n in names.split(",") if n.strip()):
            raw = headers.get(f"X-Rate-Limit-{name}") or headers.get(
                f"x-rate-limit-{name.lower()}"
            )
            if not raw:
                continue
            for clause in raw.split(","):
                parts = clause.strip().split(":")
                if len(parts) != 3:
                    continue
                try:
                    limit, period, restriction = (int(p) for p in parts)
                except ValueError:
                    continue
                rules.append(Rule(name, limit, period, restriction))
        if rules:
            self.rules = rules

    def record_request(self) -> None:
        self._history.append(self._clock())

    def before_request(self) -> None:
        if not self.rules:
            return
        while True:
            wait = self._wait_needed()
            if wait <= 0:
                return
            self._announce(wait, "rate limit")
            self._sleep(wait)

    def _wait_needed(self) -> float:
        now = self._clock()
        longest = max(r.period for r in self.rules)
        while self._history and now - self._history[0] > longest:
            self._history.popleft()

        waits = []
        for rule in self.rules:
            in_window = [t for t in self._history if now - t < rule.period]
            if len(in_window) >= rule.limit:
                waits.append(rule.period - (now - min(in_window)))
        return max(waits) if waits else 0.0

    def on_429(self, retry_after: float | None) -> None:
        self._consecutive_429 += 1
        if retry_after is not None:
            self._announce(retry_after, "429, server asked us to wait")
            self._sleep(retry_after)
            return
        backoff = min(BASE_BACKOFF * (2 ** (self._consecutive_429 - 1)), MAX_BACKOFF)
        self._announce(backoff, "429, backing off")
        self._sleep(backoff)

    def on_success(self) -> None:
        self._consecutive_429 = 0

    def _announce(self, seconds: float, reason: str) -> None:
        self._on_wait(seconds, f"{self._name} {reason}" if self._name else reason)
