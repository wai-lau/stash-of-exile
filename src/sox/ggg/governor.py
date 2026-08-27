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

The -state header is the server's count, and it wins over the local
history: the limit is per IP, so it also counts the browser, an overlay,
a second sox, and the run before this one.

    x-rate-limit-ip-state: 1:10:0,1:60:0,1:300:0,53:21600:0

`hits:period:restricted` per clause; restricted > 0 means a penalty is
running and every request until it lapses only earns a longer one.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass

BASE_BACKOFF = 5.0
MAX_BACKOFF = 300.0
# A wait this long is a lockout, not a pause — the 30:300:1800 clause hands
# out half an hour. Sleeping it stalls the watch loop with copies queueing
# behind it; instead the wait is recorded, the call refused, and the caller
# prices without the search until it lapses.
SEARCH_DOWN_AFTER = 60.0


def _header(headers: Mapping[str, str], key: str) -> str | None:
    return headers.get(f"X-Rate-Limit-{key}") or headers.get(
        f"x-rate-limit-{key.lower()}"
    )


@dataclass(frozen=True)
class Rule:
    name: str
    limit: int
    period: int
    restriction: int


@dataclass(frozen=True)
class Budget:
    """What the tightest clause has left, for the watch status line."""
    remaining: int
    limit: int
    period: int


def _clauses(raw: str | None) -> list[tuple[int, int, int] | None]:
    """`a:b:c,a:b:c` -> [(a, b, c), ...]; a malformed clause is None so the
    ones after it keep their place beside the rules."""
    out: list[tuple[int, int, int] | None] = []
    for clause in (raw or "").split(","):
        parts = clause.strip().split(":")
        try:
            a, b, c = (int(p) for p in parts)
        except ValueError:
            out.append(None)
            continue
        out.append((a, b, c))
    return out


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
        self._penalty_until = 0.0
        self._consecutive_429 = 0

    def observe(self, headers: Mapping[str, str]) -> None:
        names = _header(headers, "Rules")
        if not names:
            return
        rules: list[Rule] = []
        states: list[tuple[int, int, int] | None] = []
        for name in (n.strip() for n in names.split(",") if n.strip()):
            raw = _header(headers, name)
            if not raw:
                continue
            state = _clauses(_header(headers, f"{name}-State"))
            for i, clause in enumerate(_clauses(raw)):
                if clause is None:
                    continue
                limit, period, restriction = clause
                rules.append(Rule(name, limit, period, restriction))
                states.append(state[i] if i < len(state) else None)
        if rules:
            self.rules = rules
            self._reconcile(rules, states)

    def _reconcile(
        self, rules: list[Rule], states: list[tuple[int, int, int] | None]
    ) -> None:
        """Take the server's count for each clause as the truth.

        The state header is `hits:period:restricted` per clause, and its
        count includes what the local history cannot see: the trade site in
        a browser, an overlay, a second sox, and the run before this one. Too
        few local hits and the window is padded; too many and the oldest go.
        Padding lands just outside the next shorter window — the server said
        those hits are not in it — so they expire as early as the evidence
        allows and no earlier. A restriction in progress is waited out.
        """
        now = self._clock()
        shorter = 0
        for rule, state in sorted(zip(rules, states), key=lambda rs: rs[0].period):
            if state is not None:
                hits, _, restricted = state
                if restricted > 0:
                    self._penalty_until = max(self._penalty_until, now + restricted)
                local = sorted(t for t in self._history if now - t < rule.period)
                if hits > len(local):
                    self._history.extend([now - shorter] * (hits - len(local)))
                elif hits < len(local):
                    for stamp in local[: len(local) - hits]:
                        self._history.remove(stamp)
            shorter = rule.period
        self._history = deque(sorted(self._history))

    def record_request(self) -> None:
        self._history.append(self._clock())

    def before_request(self) -> None:
        if not self.rules:
            return
        while True:
            wait, reason = self._wait_needed()
            if wait <= 0:
                return
            self._announce(wait, reason)
            self._sleep(wait)

    def budget(self) -> Budget | None:
        """The clause with the least left; None until a response has taught
        the rules."""
        if not self.rules:
            return None
        now = self._clock()
        tightest: Budget | None = None
        for rule in sorted(self.rules, key=lambda r: r.period):
            used = sum(1 for t in self._history if now - t < rule.period)
            remaining = max(rule.limit - used, 0)
            if tightest is None or remaining < tightest.remaining:
                tightest = Budget(remaining, rule.limit, rule.period)
        return tightest

    def _wait_needed(self) -> tuple[float, str]:
        now = self._clock()
        penalty = self._penalty_until - now
        if penalty > 0:
            return penalty, "restricted by GGG"
        if not self.rules:
            return 0.0, "rate limit"

        longest = max(r.period for r in self.rules)
        while self._history and now - self._history[0] > longest:
            self._history.popleft()

        waits = []
        for rule in self.rules:
            in_window = [t for t in self._history if now - t < rule.period]
            if len(in_window) >= rule.limit:
                waits.append(rule.period - (now - min(in_window)))
        return (max(waits) if waits else 0.0), "rate limit"

    def on_429(self, retry_after: float | None) -> bool:
        """Wait out a 429 and say so, or record a lockout and return False."""
        self._consecutive_429 += 1
        if retry_after is not None:
            if retry_after >= SEARCH_DOWN_AFTER:
                # A lockout is recorded, not slept; a short wait is slept
                # through and leaves nothing to record.
                self._penalty_until = max(self._penalty_until,
                                          self._clock() + retry_after)
                return False
            self._announce(retry_after, "429, server asked us to wait")
            self._sleep(retry_after)
            return True
        backoff = min(BASE_BACKOFF * (2 ** (self._consecutive_429 - 1)), MAX_BACKOFF)
        self._announce(backoff, "429, backing off")
        self._sleep(backoff)
        return True

    def wait(self) -> float:
        """Seconds a request would wait right now; nothing is slept."""
        if not self.rules and self._penalty_until <= self._clock():
            return 0.0
        return max(self._wait_needed()[0], 0.0)

    def on_success(self) -> None:
        self._consecutive_429 = 0

    def _announce(self, seconds: float, reason: str) -> None:
        self._on_wait(seconds, f"{self._name} {reason}" if self._name else reason)
