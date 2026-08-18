"""Rate governor for GGG endpoints.

Limits are advertised only on live responses, so this starts permissive and
tightens as soon as it sees headers. It sleeps BEFORE issuing a call that
would breach a rule rather than reacting to a 429, because a 429 already
costs a restriction window.

Real search limits, captured live 2026-08-18:

    x-rate-limit-rules: Ip
    x-rate-limit-ip:    5:10:60,15:60:300,30:300:1800,600:21600:3600

One rule carries SEVERAL comma-separated clauses, all enforced at once.
Parsing only the first would breach the longer windows.
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
    ) -> None:
        self._clock = clock
        self._sleep = sleeper
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
            self._sleep(retry_after)
            return
        self._sleep(min(BASE_BACKOFF * (2 ** (self._consecutive_429 - 1)), MAX_BACKOFF))

    def on_success(self) -> None:
        self._consecutive_429 = 0
