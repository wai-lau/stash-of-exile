# sox (PoE2 Stash Valuator) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local CLI that prices every item in a PoE2 stash, reports what each tab is worth, and flags which items are worth listing.

**Architecture:** A one-way pipeline — read stash tabs via the legacy POESESSID endpoint, classify each item, price the bulk from a cached poe2scout index at zero API cost, then spend a bounded trade-search budget only on items where the index is wrong or absent. Every GGG request passes through a single rate-governed session; nothing above it knows about rate limits.

**Tech Stack:** Python 3.12, `uv`, `httpx` (sync), `pytest`. Stdlib `tomllib` for config and data files, stdlib `sqlite3` for cache, stdlib `argparse` for the CLI. No other runtime dependencies.

## Global Constraints

- **Python 3.12**, managed by `uv`. Package lives at `src/sox/`.
- **Runtime dependencies: `httpx` only.** Dev: `pytest`. Do not add others without changing this line.
- **POESESSID is a full account session token.** Held in memory only; sent only to `www.pathofexile.com`; redacted from every log line, exception, and snapshot; never written to the cache DB or a snapshot file.
- **No network calls in the default test suite.** Fixtures live gzipped in `tests/fixtures/`. Any live test is marked `@pytest.mark.integration` and deselected by default.
- **Never value an unpriceable item at zero.** Emit an explicit tag (`unpriced:*`). Zero under-reports, which is the failure mode least likely to be noticed.
- **All GGG traffic goes through `ggg/session.py`.** No module above it may construct its own HTTP client to a GGG host.
- **User-Agent with contact info** on every outbound request, GGG and poe2scout alike — poe2scout's README asks for it.
- **The three data files in `src/sox/data/` are generated**, not hand-edited: `mod_allowlist.toml` (92 mods), `base_allowlist.toml` (19 slots, 17 named bases), `unique_allowlist.toml` (38 uniques). Their generators are in `scripts/`.
- Prices are in **Exalted** internally; Divine is a display conversion using the ratio from the index.

## Milestone boundary

**Tasks 1–11 ship working software**: `sox tabs` and `sox value` that price currency, gems, and uniques from the index with zero trade-API calls. That already covers most stash value.

**Tasks 12–19** add the trade-search layer: rares, crafting bases, endgame items, and roll-sensitive uniques.

---

## Task 1: Project scaffold and config

**Files:**
- Create: `pyproject.toml`
- Create: `src/sox/__init__.py`
- Create: `src/sox/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Budgets(rares:int, bases:int, uniques:int, endgame:int)`, `Config(league:str|None, account:str|None, tabs:list[int]|None, status:str, budgets:Budgets, cache_path:Path, snapshot_dir:Path, user_agent:str)`, `load_config(path: Path | None = None) -> Config`, `DEFAULT_CONFIG_PATH: Path`.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "sox"
version = "0.1.0"
description = "PoE2 stash valuator"
requires-python = ">=3.12"
dependencies = ["httpx>=0.27"]

[project.scripts]
sox = "sox.cli:main"

[dependency-groups]
dev = ["pytest>=8"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/sox"]

[tool.pytest.ini_options]
testpaths = ["tests"]
markers = ["integration: hits live services; deselected by default"]
addopts = "-m 'not integration'"
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_config.py
from pathlib import Path

from sox.config import Config, load_config


def test_defaults_when_no_file(tmp_path):
    cfg = load_config(tmp_path / "missing.toml")
    assert cfg.league is None           # resolved at runtime from the index
    assert cfg.status == "online"
    assert cfg.budgets.rares == 20
    assert cfg.budgets.bases == 15
    assert cfg.budgets.jewels == 15
    assert cfg.budgets.uniques == 10
    assert "sox" in cfg.user_agent


def test_reads_values_from_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        'league = "Runes of Aldur"\n'
        'account = "someone"\n'
        'tabs = [0, 3]\n'
        "[budgets]\n"
        "rares = 5\n"
    )
    cfg = load_config(path)
    assert cfg.league == "Runes of Aldur"
    assert cfg.account == "someone"
    assert cfg.tabs == [0, 3]
    assert cfg.budgets.rares == 5
    assert cfg.budgets.bases == 15    # unspecified keys keep their default


def test_rejects_unknown_status(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('status = "whenever"\n')
    try:
        load_config(path)
    except ValueError as exc:
        assert "status" in str(exc)
    else:
        raise AssertionError("expected ValueError for an invalid status")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sox.config'`

- [ ] **Step 4: Implement `src/sox/config.py`**

```python
"""Configuration loading. Pure data — no I/O beyond reading the TOML file."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

DEFAULT_CONFIG_PATH = Path.home() / ".config" / "sox" / "config.toml"
DEFAULT_CACHE_PATH = Path.home() / ".local" / "share" / "sox" / "cache.sqlite"
DEFAULT_SNAPSHOT_DIR = Path.home() / ".local" / "share" / "sox" / "snapshots"

# Verified against /api/trade2/data/filters -> status_filters.status
VALID_STATUS = ("available", "securable", "onlineleague", "online", "any")

USER_AGENT = "sox/0.1 (personal stash valuator; +https://github.com/wai-lau/stash-of-exile)"


@dataclass(frozen=True)
class Budgets:
    """Search budget per item class, counted in SEARCHES, not items.

    One item can cost 1-4 calls once the relaxation ladder and the fetch are
    included. Splitting per class stops cheap base searches from starving the
    rare searches.
    """

    rares: int = 20
    bases: int = 15
    jewels: int = 15      # a stash holds many, and they are cheap to search
    uniques: int = 10
    endgame: int = 10


@dataclass(frozen=True)
class Config:
    league: str | None = None       # None -> resolve from the index at runtime
    account: str | None = None
    tabs: list[int] | None = None   # None -> all tabs
    status: str = "online"
    budgets: Budgets = Budgets()
    cache_path: Path = DEFAULT_CACHE_PATH
    snapshot_dir: Path = DEFAULT_SNAPSHOT_DIR
    user_agent: str = USER_AGENT


def load_config(path: Path | None = None) -> Config:
    path = path or DEFAULT_CONFIG_PATH
    if not path.exists():
        return Config()

    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    budgets = Budgets(**{k: int(v) for k, v in (raw.get("budgets") or {}).items()})
    cfg = Config(
        league=raw.get("league"),
        account=raw.get("account"),
        tabs=raw.get("tabs"),
        status=raw.get("status", "online"),
        budgets=budgets,
    )
    if cfg.status not in VALID_STATUS:
        raise ValueError(f"invalid status {cfg.status!r}; expected one of {VALID_STATUS}")
    if "cache_path" in raw:
        cfg = replace(cfg, cache_path=Path(raw["cache_path"]).expanduser())
    if "snapshot_dir" in raw:
        cfg = replace(cfg, snapshot_dir=Path(raw["snapshot_dir"]).expanduser())
    return cfg
```

- [ ] **Step 5: Create `src/sox/__init__.py`**

```python
"""sox — a PoE2 stash valuator."""

__version__ = "0.1.0"
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: 3 passed

- [ ] **Step 7: Confirm the existing drift suite still passes**

Run: `uv run pytest -q`
Expected: 13 passed (3 new + 10 existing drift tests)

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/sox/__init__.py src/sox/config.py tests/test_config.py
git commit -m "feat(config): project scaffold and TOML config loading"
```

---

## Task 2: POESESSID handling

**Files:**
- Create: `src/sox/secrets.py`
- Test: `tests/test_secrets.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `SecretError(Exception)`, `load_poesessid(env: Mapping[str, str] | None = None, secrets_path: Path | None = None) -> str`, `Redactor(secret: str)` with `.scrub(text: str) -> str`.

Read the spec's "Secret handling" section before writing this. The permission check is a real requirement, not decoration: a world-readable file holding this token is equivalent to a world-readable password.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_secrets.py
import pytest

from sox.secrets import Redactor, SecretError, load_poesessid


def test_prefers_environment(tmp_path):
    assert load_poesessid(env={"POESESSID": "abc123"}, secrets_path=tmp_path / "none") == "abc123"


def test_falls_back_to_secrets_file(tmp_path):
    path = tmp_path / "secrets"
    path.write_text('export POESESSID="fromfile"\n')
    path.chmod(0o600)
    assert load_poesessid(env={}, secrets_path=path) == "fromfile"


def test_rejects_group_or_world_readable_file(tmp_path):
    path = tmp_path / "secrets"
    path.write_text("POESESSID=leaky\n")
    path.chmod(0o644)
    with pytest.raises(SecretError) as exc:
        load_poesessid(env={}, secrets_path=path)
    assert "permissions" in str(exc.value)
    assert "leaky" not in str(exc.value)   # never echo the secret in an error


def test_raises_when_absent(tmp_path):
    with pytest.raises(SecretError):
        load_poesessid(env={}, secrets_path=tmp_path / "missing")


def test_redactor_scrubs_every_occurrence():
    scrubbed = Redactor("s3cret").scrub("cookie=s3cret; retry with s3cret")
    assert "s3cret" not in scrubbed
    assert scrubbed.count("<POESESSID:REDACTED>") == 2


def test_redactor_ignores_empty_secret():
    assert Redactor("").scrub("nothing to do") == "nothing to do"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_secrets.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sox.secrets'`

- [ ] **Step 3: Implement `src/sox/secrets.py`**

```python
"""POESESSID resolution and redaction.

POESESSID is a full account session token: whoever holds it is logged in as
the account owner without a password. Everything here exists to keep it out
of files, logs, and error messages.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Mapping
from pathlib import Path

DEFAULT_SECRETS_PATH = Path.home() / ".secrets"
PLACEHOLDER = "<POESESSID:REDACTED>"

# Matches `POESESSID=value`, `export POESESSID="value"`, single or double quoted.
_LINE = re.compile(r"^\s*(?:export\s+)?POESESSID\s*=\s*[\"']?([^\"'\s]+)[\"']?\s*$", re.M)


class SecretError(Exception):
    """Raised when the session token cannot be loaded safely."""


class Redactor:
    """Removes the secret from any text on its way to a log, error, or file."""

    def __init__(self, secret: str) -> None:
        self._secret = secret

    def scrub(self, text: str) -> str:
        if not self._secret:
            return text
        return text.replace(self._secret, PLACEHOLDER)


def load_poesessid(
    env: Mapping[str, str] | None = None,
    secrets_path: Path | None = None,
) -> str:
    env = os.environ if env is None else env
    from_env = env.get("POESESSID")
    if from_env:
        return from_env

    path = secrets_path or DEFAULT_SECRETS_PATH
    if not path.exists():
        raise SecretError(
            "POESESSID not found. Set the POESESSID environment variable, or add "
            f"POESESSID=... to {path} with mode 600."
        )

    mode = path.stat().st_mode
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        raise SecretError(
            f"refusing to read {path}: permissions are group- or world-readable. "
            "This file holds a full account session token. Run: chmod 600 "
            f"{path}"
        )

    match = _LINE.search(path.read_text())
    if not match:
        raise SecretError(f"no POESESSID entry found in {path}")
    return match.group(1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_secrets.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/sox/secrets.py tests/test_secrets.py
git commit -m "feat(secrets): POESESSID loading with permission check and redaction"
```

---

## Task 3: SQLite cache with TTLs

**Files:**
- Create: `src/sox/cache.py`
- Test: `tests/test_cache.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Cache(path: Path, clock: Callable[[], float] = time.time)` with `.get(table: str, key: str) -> Any | None`, `.put(table: str, key: str, value: Any, ttl: int) -> None`, `.close() -> None`; `TTL: dict[str, int]`.

`clock` is injected so TTL expiry is tested without sleeping.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cache.py
from sox.cache import TTL, Cache


def test_round_trips_json_values(tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    cache.put("index_price", "Mageblood", {"price": 135416.55}, ttl=60)
    assert cache.get("index_price", "Mageblood") == {"price": 135416.55}
    cache.close()


def test_missing_key_returns_none(tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    assert cache.get("index_price", "nope") is None
    cache.close()


def test_expired_entry_returns_none(tmp_path):
    now = [1000.0]
    cache = Cache(tmp_path / "c.sqlite", clock=lambda: now[0])
    cache.put("index_price", "k", "v", ttl=10)
    now[0] = 1009.0
    assert cache.get("index_price", "k") == "v"
    now[0] = 1011.0
    assert cache.get("index_price", "k") is None
    cache.close()


def test_put_overwrites_and_refreshes_expiry(tmp_path):
    now = [0.0]
    cache = Cache(tmp_path / "c.sqlite", clock=lambda: now[0])
    cache.put("index_price", "k", "old", ttl=10)
    now[0] = 9.0
    cache.put("index_price", "k", "new", ttl=10)
    now[0] = 15.0
    assert cache.get("index_price", "k") == "new"
    cache.close()


def test_persists_across_instances(tmp_path):
    path = tmp_path / "c.sqlite"
    first = Cache(path)
    first.put("stats_data", "stats", [1, 2, 3], ttl=3600)
    first.close()
    second = Cache(path)
    assert second.get("stats_data", "stats") == [1, 2, 3]
    second.close()


def test_ttls_match_the_spec():
    assert TTL["stats_data"] == 7 * 86400
    assert TTL["filters_data"] == 7 * 86400
    assert TTL["index_price"] == 6 * 3600
    assert TTL["trade_price"] == 12 * 3600
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_cache.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sox.cache'`

- [ ] **Step 3: Implement `src/sox/cache.py`**

```python
"""On-disk cache. A cache hit costs no API budget, which is the whole point."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

TTL = {
    "filters_data": 7 * 86400,
    "stats_data": 7 * 86400,
    "index_price": 6 * 3600,
    "trade_price": 12 * 3600,
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    tbl        TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    expires_at REAL NOT NULL,
    PRIMARY KEY (tbl, key)
)
"""


class Cache:
    """Never store a secret here. Prices and API metadata only."""

    def __init__(self, path: Path, clock: Callable[[], float] = time.time) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._conn = sqlite3.connect(path)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def get(self, table: str, key: str) -> Any | None:
        row = self._conn.execute(
            "SELECT value, expires_at FROM entries WHERE tbl = ? AND key = ?",
            (table, key),
        ).fetchone()
        if row is None:
            return None
        value, expires_at = row
        if self._clock() > expires_at:
            return None
        return json.loads(value)

    def put(self, table: str, key: str, value: Any, ttl: int) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO entries (tbl, key, value, expires_at) VALUES (?, ?, ?, ?)",
            (table, key, json.dumps(value), self._clock() + ttl),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cache.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/sox/cache.py tests/test_cache.py
git commit -m "feat(cache): SQLite cache with per-table TTLs"
```

---

## Task 4: Rate governor

**Files:**
- Create: `src/sox/ggg/__init__.py`
- Create: `src/sox/ggg/governor.py`
- Test: `tests/test_governor.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Rule(name: str, limit: int, period: int, restriction: int)`, `RateGovernor(clock=time.monotonic, sleeper=time.sleep)` with `.observe(headers: Mapping[str, str]) -> None`, `.before_request() -> None`, `.on_429(retry_after: float | None) -> None`, `.rules: list[Rule]`.

Rate limit rules are only discoverable from a live response, so the governor starts permissive and tightens as soon as it sees headers. Header format, per the spec: `X-Rate-Limit-Rules: Account,Ip`, then `X-Rate-Limit-Account: 15:60:60` (limit:period:restriction) and `X-Rate-Limit-Account-State: 3:60:0` (current:period:restricted).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_governor.py
from sox.ggg.governor import RateGovernor


def make(clock_start=0.0):
    now = [clock_start]
    slept = []

    def sleeper(seconds):
        slept.append(seconds)
        now[0] += seconds

    gov = RateGovernor(clock=lambda: now[0], sleeper=sleeper)
    return gov, now, slept


def test_no_sleep_before_any_headers_seen():
    gov, _, slept = make()
    gov.before_request()
    assert slept == []


def test_parses_rules_from_headers():
    gov, _, _ = make()
    gov.observe({
        "X-Rate-Limit-Rules": "Account,Ip",
        "X-Rate-Limit-Account": "15:60:60",
        "X-Rate-Limit-Account-State": "1:60:0",
        "X-Rate-Limit-Ip": "8:10:30",
        "X-Rate-Limit-Ip-State": "1:10:0",
    })
    assert {r.name for r in gov.rules} == {"Account", "Ip"}
    assert [r for r in gov.rules if r.name == "Ip"][0].limit == 8


def test_sleeps_before_breaching_the_tightest_rule():
    gov, now, slept = make()
    gov.observe({
        "X-Rate-Limit-Rules": "Ip",
        "X-Rate-Limit-Ip": "2:10:30",
        "X-Rate-Limit-Ip-State": "0:10:0",
    })
    for _ in range(2):
        gov.before_request()
        gov.record_request()
    assert slept == []
    gov.before_request()          # third request inside a 2-per-10s window
    assert slept and slept[0] > 0


def test_window_slides_so_old_requests_stop_counting():
    gov, now, slept = make()
    gov.observe({
        "X-Rate-Limit-Rules": "Ip",
        "X-Rate-Limit-Ip": "2:10:30",
        "X-Rate-Limit-Ip-State": "0:10:0",
    })
    for _ in range(2):
        gov.before_request()
        gov.record_request()
    now[0] += 11                  # both fall out of the window
    gov.before_request()
    assert slept == []


def test_429_honours_retry_after():
    gov, _, slept = make()
    gov.on_429(retry_after=7.5)
    assert slept == [7.5]


def test_429_without_retry_after_still_backs_off():
    gov, _, slept = make()
    gov.on_429(retry_after=None)
    assert slept and slept[0] > 0


def test_repeated_429s_back_off_further():
    gov, _, slept = make()
    gov.on_429(None)
    gov.on_429(None)
    assert slept[1] > slept[0]


def test_malformed_headers_are_ignored_not_fatal():
    gov, _, slept = make()
    gov.observe({"X-Rate-Limit-Rules": "Ip", "X-Rate-Limit-Ip": "garbage"})
    gov.before_request()
    assert slept == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_governor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sox.ggg'`

- [ ] **Step 3: Implement `src/sox/ggg/governor.py`**

```python
"""Rate governor for GGG endpoints.

The API advertises its limits only on live responses, so this starts
permissive and tightens the moment it sees headers. It sleeps BEFORE issuing
a call that would breach a rule, rather than reacting to a 429 after the
fact — a 429 already costs a restriction window.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass

# Used when a 429 arrives with no Retry-After header.
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
        """Learn the rules from a live response. Malformed headers are ignored."""
        names = headers.get("X-Rate-Limit-Rules")
        if not names:
            return
        rules = []
        for name in (n.strip() for n in names.split(",") if n.strip()):
            raw = headers.get(f"X-Rate-Limit-{name}")
            if not raw:
                continue
            for clause in raw.split(","):
                parts = clause.split(":")
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
        """Sleep until issuing one more request breaches no known rule."""
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
            # `- 1` leaves room for the request we are about to make.
            if len(in_window) >= rule.limit:
                oldest = min(in_window)
                waits.append(rule.period - (now - oldest))
        return max(waits) if waits else 0.0

    def on_429(self, retry_after: float | None) -> None:
        self._consecutive_429 += 1
        if retry_after is not None:
            self._sleep(retry_after)
            return
        backoff = min(BASE_BACKOFF * (2 ** (self._consecutive_429 - 1)), MAX_BACKOFF)
        self._sleep(backoff)

    def on_success(self) -> None:
        self._consecutive_429 = 0
```

- [ ] **Step 4: Create `src/sox/ggg/__init__.py`**

```python
"""GGG API clients. All outbound GGG traffic passes through session.py."""
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_governor.py -v`
Expected: 8 passed

- [ ] **Step 6: Commit**

```bash
git add src/sox/ggg/__init__.py src/sox/ggg/governor.py tests/test_governor.py
git commit -m "feat(governor): rate governor that sleeps before breaching a rule"
```

---

## Task 5: GGG session

**Files:**
- Create: `src/sox/ggg/session.py`
- Test: `tests/test_session.py`

**Interfaces:**
- Consumes: `RateGovernor` (Task 4), `Redactor` (Task 2).
- Produces: `GGGError`, `AuthExpired`, `Blocked`, `RateLimited`; `GGGSession(poesessid: str, governor: RateGovernor, client: httpx.Client, user_agent: str)` with `.get(url: str, **kw) -> httpx.Response` and `.post(url: str, json: Any = None, **kw) -> httpx.Response`.

Tests use `httpx.MockTransport` — no network, no extra dependency.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_session.py
import httpx
import pytest

from sox.ggg.governor import RateGovernor
from sox.ggg.session import AuthExpired, Blocked, GGGSession


def build(handler, sleeper=None):
    gov = RateGovernor(clock=lambda: 0.0, sleeper=sleeper or (lambda s: None))
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return GGGSession("s3cret", gov, client, user_agent="sox-test")


def test_sends_cookie_and_user_agent():
    seen = {}

    def handler(request):
        seen["cookie"] = request.headers.get("cookie")
        seen["ua"] = request.headers.get("user-agent")
        return httpx.Response(200, json={"ok": True})

    build(handler).get("https://www.pathofexile.com/x")
    assert "POESESSID=s3cret" in seen["cookie"]
    assert seen["ua"] == "sox-test"


def test_login_redirect_raises_auth_expired():
    def handler(request):
        return httpx.Response(302, headers={"location": "https://www.pathofexile.com/login"})

    with pytest.raises(AuthExpired):
        build(handler).get("https://www.pathofexile.com/x")


def test_403_raises_blocked_with_actionable_message():
    def handler(request):
        return httpx.Response(403, text="cloudflare")

    with pytest.raises(Blocked) as exc:
        build(handler).get("https://www.pathofexile.com/x")
    assert "cookie" in str(exc.value).lower()


def test_secret_never_appears_in_an_error():
    def handler(request):
        return httpx.Response(500, text="failed for cookie POESESSID=s3cret")

    with pytest.raises(Exception) as exc:
        build(handler).get("https://www.pathofexile.com/x")
    assert "s3cret" not in str(exc.value)


def test_429_is_retried_after_backoff():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "1"})
        return httpx.Response(200, json={"ok": True})

    slept = []
    response = build(handler, sleeper=slept.append).get("https://www.pathofexile.com/x")
    assert response.status_code == 200
    assert calls["n"] == 2
    assert 1 in slept


def test_governor_learns_limits_from_response_headers():
    def handler(request):
        return httpx.Response(200, json={}, headers={
            "X-Rate-Limit-Rules": "Ip",
            "X-Rate-Limit-Ip": "8:10:30",
            "X-Rate-Limit-Ip-State": "1:10:0",
        })

    gov = RateGovernor(clock=lambda: 0.0, sleeper=lambda s: None)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    GGGSession("s", gov, client, user_agent="t").get("https://www.pathofexile.com/x")
    assert gov.rules and gov.rules[0].limit == 8
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_session.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sox.ggg.session'`

- [ ] **Step 3: Implement `src/sox/ggg/session.py`**

```python
"""The single door to GGG. Nothing above this module may bypass it."""

from __future__ import annotations

from typing import Any

import httpx

from sox.ggg.governor import RateGovernor
from sox.secrets import Redactor

MAX_429_RETRIES = 3


class GGGError(Exception):
    """Base for every GGG transport failure."""


class AuthExpired(GGGError):
    pass


class Blocked(GGGError):
    pass


class RateLimited(GGGError):
    pass


class GGGSession:
    def __init__(
        self,
        poesessid: str,
        governor: RateGovernor,
        client: httpx.Client,
        user_agent: str,
    ) -> None:
        self._cookie = f"POESESSID={poesessid}"
        self._governor = governor
        self._client = client
        self._redactor = Redactor(poesessid)
        self._headers = {"User-Agent": user_agent, "Accept": "application/json"}

    def get(self, url: str, **kw: Any) -> httpx.Response:
        return self._request("GET", url, **kw)

    def post(self, url: str, json: Any = None, **kw: Any) -> httpx.Response:
        return self._request("POST", url, json=json, **kw)

    def _request(self, method: str, url: str, **kw: Any) -> httpx.Response:
        headers = {**self._headers, "Cookie": self._cookie, **kw.pop("headers", {})}

        for attempt in range(MAX_429_RETRIES + 1):
            self._governor.before_request()
            self._governor.record_request()
            response = self._client.request(
                method, url, headers=headers, follow_redirects=False, **kw
            )
            self._governor.observe(response.headers)

            if response.status_code == 429:
                if attempt == MAX_429_RETRIES:
                    raise RateLimited("rate limited by GGG after repeated backoff")
                retry_after = response.headers.get("Retry-After")
                self._governor.on_429(float(retry_after) if retry_after else None)
                continue

            self._governor.on_success()
            self._check(response)
            return response

        raise RateLimited("unreachable")   # pragma: no cover

    def _check(self, response: httpx.Response) -> None:
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("location", "")
            if "login" in location:
                raise AuthExpired(
                    "POESESSID has expired. Log in at pathofexile.com and copy the "
                    "new POESESSID cookie."
                )
            raise GGGError(f"unexpected redirect to {location}")

        if response.status_code == 403:
            raise Blocked(
                "403 from Cloudflare. Refresh your POESESSID cookie and confirm the "
                "User-Agent looks like a real browser session."
            )

        if response.status_code >= 400:
            body = self._redactor.scrub(response.text[:400])
            raise GGGError(f"HTTP {response.status_code} from {response.url}: {body}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_session.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/sox/ggg/session.py tests/test_session.py
git commit -m "feat(session): rate-governed GGG client with redacted errors"
```

---

## Task 6: Stash client

**Files:**
- Create: `src/sox/ggg/stash.py`
- Test: `tests/test_stash.py`
- Create: `tests/fixtures/stash_tabs.json` (synthetic, committed)
- Create: `tests/fixtures/stash_tab_items.json` (synthetic, committed)

**Interfaces:**
- Consumes: `GGGSession` (Task 5).
- Produces: `Tab(index: int, id: str, name: str, type: str)`, `StashParseError(GGGError)`, `list_tabs(session, account: str, league: str) -> list[Tab]`, `fetch_tab(session, account: str, league: str, tab_index: int) -> list[dict]`, `STASH_URL: str`.

**This is the one endpoint that could not be verified against the live service** — it needs a POESESSID. Field names below follow the documented `character-window/get-stash-items` shape used by existing PoE2 tools. Step 6 is a verification step against real data; treat the parser as provisional until it passes.

- [ ] **Step 1: Write the fixtures**

```json
// tests/fixtures/stash_tabs.json
{
  "numTabs": 3,
  "tabs": [
    {"n": "Currency", "i": 0, "id": "aaaa", "type": "CurrencyStash"},
    {"n": "Gear", "i": 1, "id": "bbbb", "type": "NormalStash"},
    {"n": "Maps", "i": 2, "id": "cccc", "type": "MapStash"}
  ],
  "items": []
}
```

```json
// tests/fixtures/stash_tab_items.json
{
  "numTabs": 3,
  "items": [
    {"typeLine": "Exalted Orb", "baseType": "Exalted Orb", "stackSize": 412, "frameType": 5},
    {"name": "Mageblood", "typeLine": "Utility Belt", "baseType": "Utility Belt",
     "frameType": 3, "ilvl": 68, "corrupted": false,
     "explicitMods": ["+21% to Chaos Resistance"]}
  ]
}
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_stash.py
import json
from pathlib import Path

import httpx
import pytest

from sox.ggg.governor import RateGovernor
from sox.ggg.session import GGGSession
from sox.ggg.stash import StashParseError, fetch_tab, list_tabs

FIXTURES = Path(__file__).parent / "fixtures"


def session_for(payload, capture=None):
    def handler(request):
        if capture is not None:
            capture["url"] = str(request.url)
        return httpx.Response(200, json=payload)

    gov = RateGovernor(clock=lambda: 0.0, sleeper=lambda s: None)
    return GGGSession("s", gov, httpx.Client(transport=httpx.MockTransport(handler)), "t")


def test_lists_tabs_with_index_and_type():
    payload = json.loads((FIXTURES / "stash_tabs.json").read_text())
    tabs = list_tabs(session_for(payload), account="me", league="Runes of Aldur")
    assert [t.index for t in tabs] == [0, 1, 2]
    assert tabs[0].name == "Currency"
    assert tabs[2].type == "MapStash"


def test_request_targets_the_poe2_realm():
    payload = json.loads((FIXTURES / "stash_tabs.json").read_text())
    capture = {}
    list_tabs(session_for(payload, capture), account="me", league="Runes of Aldur")
    assert "realm=poe2" in capture["url"]
    assert "accountName=me" in capture["url"]


def test_fetches_items_for_a_tab():
    payload = json.loads((FIXTURES / "stash_tab_items.json").read_text())
    items = fetch_tab(session_for(payload), account="me", league="L", tab_index=1)
    assert len(items) == 2
    assert items[0]["stackSize"] == 412


def test_missing_items_key_raises_rather_than_reporting_empty():
    """A tab shape we cannot parse must fail loudly.

    Silently returning [] would make an unparsed tab look like an empty one,
    and the run would report a confidently wrong total.
    """
    with pytest.raises(StashParseError):
        fetch_tab(session_for({"numTabs": 3}), account="me", league="L", tab_index=0)


def test_empty_tab_is_allowed_when_items_key_is_present():
    items = fetch_tab(session_for({"items": []}), account="me", league="L", tab_index=0)
    assert items == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_stash.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sox.ggg.stash'`

- [ ] **Step 4: Implement `src/sox/ggg/stash.py`**

```python
"""Legacy stash endpoint.

The official OAuth API has no PoE2 stash endpoint — /stash is PoE1-only, and
only /character supports the poe2 realm. So reads go through the legacy
character-window route with a POESESSID cookie.

This endpoint is unofficial and may change without notice. It is deliberately
the only module that knows its shape: when it breaks, it breaks here.
"""

from __future__ import annotations

from dataclasses import dataclass

from sox.ggg.session import GGGError, GGGSession

STASH_URL = "https://www.pathofexile.com/character-window/get-stash-items"
REALM = "poe2"


class StashParseError(GGGError):
    """The response did not look like a stash payload."""


@dataclass(frozen=True)
class Tab:
    index: int
    id: str
    name: str
    type: str


def list_tabs(session: GGGSession, account: str, league: str) -> list[Tab]:
    payload = session.get(
        STASH_URL,
        params={
            "accountName": account,
            "realm": REALM,
            "league": league,
            "tabs": 1,
            "tabIndex": 0,
        },
    ).json()

    raw_tabs = payload.get("tabs")
    if raw_tabs is None:
        raise StashParseError(
            "no 'tabs' key in the stash response — the endpoint shape has probably "
            "changed, or the account name is wrong"
        )
    return [
        Tab(
            index=tab.get("i", position),
            id=tab.get("id", ""),
            name=tab.get("n", ""),
            type=tab.get("type", ""),
        )
        for position, tab in enumerate(raw_tabs)
    ]


def fetch_tab(session: GGGSession, account: str, league: str, tab_index: int) -> list[dict]:
    payload = session.get(
        STASH_URL,
        params={
            "accountName": account,
            "realm": REALM,
            "league": league,
            "tabs": 0,
            "tabIndex": tab_index,
        },
    ).json()

    items = payload.get("items")
    if items is None:
        # Never return [] here. An unparsed tab must not masquerade as an
        # empty one — that turns a parsing bug into a silently wrong total.
        raise StashParseError(
            f"tab {tab_index} returned no 'items' key. Special tabs "
            "(currency/map/gem) may use a different shape; capture the payload "
            "and extend this parser rather than treating the tab as empty."
        )
    return items
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_stash.py -v`
Expected: 5 passed

- [ ] **Step 6: Verify against real data (requires the account owner)**

This parser is provisional until it has seen a real payload. Ask the owner to run, with their own session:

```bash
curl -sS -b "POESESSID=$POESESSID" \
  -A 'sox/0.1 (personal stash valuator)' \
  'https://www.pathofexile.com/character-window/get-stash-items?accountName=<ACCOUNT>&realm=poe2&league=<LEAGUE>&tabs=1&tabIndex=0' \
  | python3 -m json.tool | head -40
```

Compare the `tabs[]` keys (`n`, `i`, `id`, `type`) against the fixture. If they differ, update `list_tabs` and the fixture together. Then repeat with `tabs=0&tabIndex=<a special tab>` to capture a currency or map tab and confirm whether `items` is present.

**Do not commit a real payload** — it identifies the account. Hand-edit it into the synthetic fixture shape instead.

- [ ] **Step 7: Commit**

```bash
git add src/sox/ggg/stash.py tests/test_stash.py tests/fixtures/stash_tabs.json tests/fixtures/stash_tab_items.json
git commit -m "feat(stash): legacy poe2 stash reader that fails loudly on unknown shapes"
```

---

## Task 7: poe2scout index client

**Files:**
- Create: `src/sox/scout.py`
- Test: `tests/test_scout.py`

**Interfaces:**
- Consumes: `Cache` (Task 3).
- Produces: `League(value: str, short: str, divine_price_ex: float, base_currency: str)`, `IndexEntry(name: str, price_ex: float, quantity: int, metadata: dict)`, `ScoutClient(client: httpx.Client, cache: Cache, user_agent: str)` with `.current_league() -> League`, `.prices(league: str) -> dict[str, IndexEntry]`; `CURRENCY_CATEGORIES: tuple[str, ...]`, `UNIQUE_CATEGORIES: tuple[str, ...]`.

Endpoints and categories below are verified live. `category` is a required query parameter — omitting it returns HTTP 400.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scout.py
import httpx

from sox.cache import Cache
from sox.scout import CURRENCY_CATEGORIES, UNIQUE_CATEGORIES, ScoutClient

LEAGUES = [
    {"Value": "Standard", "ShortName": "standard", "IsCurrent": False,
     "DivinePrice": 230.8, "BaseCurrencyText": "Exalted Orb"},
    {"Value": "Runes of Aldur", "ShortName": "runes", "IsCurrent": True,
     "DivinePrice": 336.5, "BaseCurrencyText": "Exalted Orb"},
]


def client_for(handler, tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    return ScoutClient(httpx.Client(transport=httpx.MockTransport(handler)), cache, "sox-test")


def test_resolves_the_current_league(tmp_path):
    def handler(request):
        return httpx.Response(200, json=LEAGUES)

    league = client_for(handler, tmp_path).current_league()
    assert league.value == "Runes of Aldur"
    assert league.short == "runes"
    assert league.divine_price_ex == 336.5


def test_sends_contact_user_agent(tmp_path):
    seen = {}

    def handler(request):
        seen["ua"] = request.headers.get("user-agent")
        return httpx.Response(200, json=LEAGUES)

    client_for(handler, tmp_path).current_league()
    assert seen["ua"] == "sox-test"


def test_prices_merge_every_category_and_include_category_param(tmp_path):
    seen = []

    def handler(request):
        seen.append(request.url.params.get("category"))
        name = f"item-{request.url.params.get('category')}"
        return httpx.Response(200, json={"Items": [
            {"Name": name, "Text": name, "CurrentPrice": 1.5,
             "CurrentQuantity": 7, "ItemMetadata": {}},
        ]})

    prices = client_for(handler, tmp_path).prices("runes")
    assert set(seen) == set(CURRENCY_CATEGORIES) | set(UNIQUE_CATEGORIES)
    assert prices["item-currency"].price_ex == 1.5
    assert prices["item-currency"].quantity == 7


def test_second_call_is_served_from_cache(tmp_path):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={"Items": [
            {"Name": "x", "CurrentPrice": 2.0, "CurrentQuantity": 1, "ItemMetadata": {}},
        ]})

    cache = Cache(tmp_path / "c.sqlite")
    transport = httpx.MockTransport(handler)
    first = ScoutClient(httpx.Client(transport=transport), cache, "t")
    first.prices("runes")
    calls_after_first = calls["n"]

    second = ScoutClient(httpx.Client(transport=transport), cache, "t")
    second.prices("runes")
    assert calls["n"] == calls_after_first, "cached prices must not re-request"


def test_gem_levels_are_distinct_keys(tmp_path):
    """Uncut gems price by level: L20 ~1595ex vs L4 ~200ex."""
    def handler(request):
        if request.url.params.get("category") != "uncutgems":
            return httpx.Response(200, json={"Items": []})
        return httpx.Response(200, json={"Items": [
            {"Name": "Uncut Skill Gem", "Text": "Uncut Skill Gem (Level 20)",
             "CurrentPrice": 1595.5, "CurrentQuantity": 385, "ItemMetadata": {}},
            {"Name": "Uncut Skill Gem", "Text": "Uncut Skill Gem (Level 4)",
             "CurrentPrice": 199.8, "CurrentQuantity": 19, "ItemMetadata": {}},
        ]})

    prices = client_for(handler, tmp_path).prices("runes")
    assert prices["Uncut Skill Gem (Level 20)"].price_ex == 1595.5
    assert prices["Uncut Skill Gem (Level 4)"].price_ex == 199.8
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scout.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sox.scout'`

- [ ] **Step 3: Implement `src/sox/scout.py`**

```python
"""poe2scout index client — the zero-cost half of pricing.

No auth, no key. Their published Swagger is misconfigured (it points at the
Swagger petstore demo), so these routes were read from the project source and
then confirmed against the live service. `category` is REQUIRED; omitting it
returns HTTP 400.

Their README asks for a User-Agent with contact info for sustained use.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from sox.cache import TTL, Cache

BASE = "https://api.poe2scout.com/poe2"

CURRENCY_CATEGORIES = (
    "currency", "fragments", "runes", "essences", "ultimatum", "expedition",
    "ritual", "vaultkeys", "breach", "abyss", "uncutgems", "lineagesupportgems",
    "delirium", "incursion", "idol", "verisium", "vaal",
)
UNIQUE_CATEGORIES = ("accessory", "armour", "flask", "jewel", "map", "weapon", "sanctum")


@dataclass(frozen=True)
class League:
    value: str
    short: str
    divine_price_ex: float
    base_currency: str


@dataclass(frozen=True)
class IndexEntry:
    name: str
    price_ex: float
    quantity: int
    metadata: dict


class ScoutClient:
    def __init__(self, client: httpx.Client, cache: Cache, user_agent: str) -> None:
        self._client = client
        self._cache = cache
        self._headers = {"User-Agent": user_agent, "Accept": "application/json"}

    def current_league(self) -> League:
        leagues = self._get("/Leagues")
        for entry in leagues:
            if entry.get("IsCurrent"):
                return League(
                    value=entry["Value"],
                    short=entry["ShortName"],
                    divine_price_ex=float(entry.get("DivinePrice") or 0.0),
                    base_currency=entry.get("BaseCurrencyText", "Exalted Orb"),
                )
        raise RuntimeError("no current league reported by the index")

    def prices(self, league: str) -> dict[str, IndexEntry]:
        """Every indexable item, keyed by display name.

        `Text` is preferred over `Name` because it carries the distinguishing
        detail for level-priced items — "Uncut Skill Gem (Level 20)" and
        "(Level 4)" share a Name but differ 8x in price.
        """
        cached = self._cache.get("index_price", league)
        if cached is not None:
            return {k: IndexEntry(**v) for k, v in cached.items()}

        merged: dict[str, IndexEntry] = {}
        for kind, categories in (
            ("Currencies", CURRENCY_CATEGORIES),
            ("Uniques", UNIQUE_CATEGORIES),
        ):
            for category in categories:
                payload = self._get(
                    f"/Leagues/{league}/{kind}/ByCategory",
                    params={"category": category, "perPage": 100},
                )
                for item in payload.get("Items", []):
                    price = item.get("CurrentPrice")
                    if price is None:
                        continue
                    name = item.get("Text") or item.get("Name")
                    if not name:
                        continue
                    merged[name] = IndexEntry(
                        name=name,
                        price_ex=float(price),
                        quantity=int(item.get("CurrentQuantity") or 0),
                        metadata=item.get("ItemMetadata") or {},
                    )

        self._cache.put(
            "index_price", league,
            {k: v.__dict__ for k, v in merged.items()},
            ttl=TTL["index_price"],
        )
        return merged

    def _get(self, path: str, params: dict | None = None):
        response = self._client.get(BASE + path, headers=self._headers, params=params)
        response.raise_for_status()
        return response.json()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_scout.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/sox/scout.py tests/test_scout.py
git commit -m "feat(scout): cached poe2scout index client"
```

---

## Task 8: Item classification

**Files:**
- Create: `src/sox/valuation/__init__.py`
- Create: `src/sox/valuation/classify.py`
- Test: `tests/test_classify.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ItemClass` (StrEnum: `CURRENCY`, `GEM`, `UNIQUE`, `GEAR`, `ENDGAME`, `UNKNOWN`), `Rarity` (StrEnum: `NORMAL`, `MAGIC`, `RARE`, `UNIQUE`), `classify(item: dict) -> ItemClass`, `rarity_of(item: dict) -> Rarity | None`, `display_name(item: dict) -> str`.

Handles both shapes: `frameType` int (0 normal, 1 magic, 2 rare, 3 unique, 4 gem, 5 currency) and a `rarity` string, because the live shape is unconfirmed (see Task 6).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_classify.py
from sox.valuation.classify import ItemClass, Rarity, classify, display_name, rarity_of


def test_classifies_by_frame_type():
    assert classify({"typeLine": "Exalted Orb", "frameType": 5}) is ItemClass.CURRENCY
    assert classify({"typeLine": "Spark", "frameType": 4}) is ItemClass.GEM
    assert classify({"name": "Mageblood", "frameType": 3}) is ItemClass.UNIQUE
    assert classify({"typeLine": "Vaal Greaves", "frameType": 2}) is ItemClass.GEAR


def test_classifies_by_rarity_string_when_frame_type_absent():
    assert classify({"typeLine": "Vaal Greaves", "rarity": "Rare"}) is ItemClass.GEAR
    assert classify({"name": "Mageblood", "rarity": "Unique"}) is ItemClass.UNIQUE


def test_waystones_and_tablets_are_endgame_not_gear():
    """No index prices these, so they must not fall into the gear path."""
    assert classify({"typeLine": "Waystone (Tier 15)", "frameType": 2}) is ItemClass.ENDGAME
    assert classify({"typeLine": "Breach Tablet", "frameType": 2}) is ItemClass.ENDGAME
    assert classify({"typeLine": "Amphora Relic", "frameType": 2}) is ItemClass.ENDGAME
    assert classify({"typeLine": "Topaz Charm", "frameType": 2}) is ItemClass.ENDGAME


def test_lineage_support_gems_classify_as_gem():
    item = {"typeLine": "Uul-Netol's Embrace", "frameType": 4}
    assert classify(item) is ItemClass.GEM


def test_unrecognised_item_is_unknown_not_gear():
    """Unknown must never be priced as something else, nor as zero."""
    assert classify({"typeLine": "Ornate Wombgift"}) is ItemClass.UNKNOWN


def test_rarity_of_reads_both_shapes():
    assert rarity_of({"frameType": 0}) is Rarity.NORMAL
    assert rarity_of({"frameType": 1}) is Rarity.MAGIC
    assert rarity_of({"rarity": "Magic"}) is Rarity.MAGIC
    assert rarity_of({}) is None


def test_display_name_prefers_unique_name_then_type():
    assert display_name({"name": "Mageblood", "typeLine": "Utility Belt"}) == "Mageblood"
    assert display_name({"typeLine": "Vaal Greaves"}) == "Vaal Greaves"
    assert display_name({"baseType": "Vaal Greaves"}) == "Vaal Greaves"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_classify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sox.valuation'`

- [ ] **Step 3: Implement `src/sox/valuation/classify.py`**

```python
"""Sort a stash item into the class that decides how it gets priced.

The classes are pricing paths, not game categories:
  CURRENCY / GEM / UNIQUE -> the index prices these for free
  GEAR                    -> trade search, scored by mods and base
  ENDGAME                 -> trade search; NO index covers these at all
  UNKNOWN                 -> never priced, never zeroed, always reported
"""

from __future__ import annotations

from enum import StrEnum

# PoE1-style frame types, still used by the legacy endpoint.
FRAME_NORMAL, FRAME_MAGIC, FRAME_RARE, FRAME_UNIQUE, FRAME_GEM, FRAME_CURRENCY = range(6)

# Classes with no index coverage — see docs/research/2026-08-17-coverage-audit.md.
ENDGAME_MARKERS = ("Waystone", "Tablet", "Relic", "Charm")

# The wombgift group has neither an index price nor a clean trade category.
UNKNOWN_MARKERS = ("Wombgift",)


class ItemClass(StrEnum):
    CURRENCY = "currency"
    GEM = "gem"
    UNIQUE = "unique"
    GEAR = "gear"
    ENDGAME = "endgame"
    UNKNOWN = "unknown"


class Rarity(StrEnum):
    NORMAL = "normal"
    MAGIC = "magic"
    RARE = "rare"
    UNIQUE = "unique"


_FRAME_TO_RARITY = {
    FRAME_NORMAL: Rarity.NORMAL,
    FRAME_MAGIC: Rarity.MAGIC,
    FRAME_RARE: Rarity.RARE,
    FRAME_UNIQUE: Rarity.UNIQUE,
}


def display_name(item: dict) -> str:
    return item.get("name") or item.get("typeLine") or item.get("baseType") or "<unnamed>"


def rarity_of(item: dict) -> Rarity | None:
    frame = item.get("frameType")
    if isinstance(frame, int) and frame in _FRAME_TO_RARITY:
        return _FRAME_TO_RARITY[frame]
    raw = item.get("rarity")
    if isinstance(raw, str):
        try:
            return Rarity(raw.casefold())
        except ValueError:
            return None
    return None


def classify(item: dict) -> ItemClass:
    name = display_name(item)

    if any(marker in name for marker in UNKNOWN_MARKERS):
        return ItemClass.UNKNOWN

    frame = item.get("frameType")
    if frame == FRAME_CURRENCY:
        return ItemClass.CURRENCY
    if frame == FRAME_GEM:
        return ItemClass.GEM

    # Endgame markers win over rarity: a rare Waystone is still a waystone,
    # and pricing it down the gear path would search the wrong category.
    if any(marker in name for marker in ENDGAME_MARKERS):
        return ItemClass.ENDGAME

    rarity = rarity_of(item)
    if rarity is Rarity.UNIQUE:
        return ItemClass.UNIQUE
    if rarity in (Rarity.NORMAL, Rarity.MAGIC, Rarity.RARE):
        return ItemClass.GEAR
    return ItemClass.UNKNOWN
```

- [ ] **Step 4: Create `src/sox/valuation/__init__.py`**

```python
"""Pricing logic: classification, index lookup, candidate selection, search."""
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_classify.py -v`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add src/sox/valuation/__init__.py src/sox/valuation/classify.py tests/test_classify.py
git commit -m "feat(classify): route items to a pricing path, never to zero"
```

---

## Task 9: Index pricing

**Files:**
- Create: `src/sox/valuation/index_pricer.py`
- Test: `tests/test_index_pricer.py`

**Interfaces:**
- Consumes: `IndexEntry` (Task 7), `classify`/`display_name` (Task 8).
- Produces: `PricedItem(name: str, item_class: ItemClass, unit_price_ex: float | None, stack: int, total_ex: float | None, source: str, tag: str | None, tab: int, quantity: int, spread: float)`, `price_from_index(item: dict, tab: int, index: dict[str, IndexEntry]) -> PricedItem`, `gem_index_key(item: dict) -> str`.

`source` is one of `index`, `unpriced`. `tag` is `None` or `unpriced:no-index`, `unpriced:unknown-class`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_index_pricer.py
from sox.scout import IndexEntry
from sox.valuation.classify import ItemClass
from sox.valuation.index_pricer import gem_index_key, price_from_index


def entry(name, price, quantity=10, metadata=None):
    return IndexEntry(name=name, price_ex=price, quantity=quantity, metadata=metadata or {})


INDEX = {
    "Exalted Orb": entry("Exalted Orb", 1.0),
    "Mageblood": entry("Mageblood", 135416.55, quantity=5808),
    "Uul-Netol's Embrace": entry("Uul-Netol's Embrace", 150826.1, quantity=3),
    "Uncut Skill Gem (Level 20)": entry("Uncut Skill Gem (Level 20)", 1595.5),
}


def test_currency_multiplies_by_stack_size():
    item = {"typeLine": "Exalted Orb", "frameType": 5, "stackSize": 412}
    priced = price_from_index(item, tab=0, index=INDEX)
    assert priced.unit_price_ex == 1.0
    assert priced.stack == 412
    assert priced.total_ex == 412.0


def test_missing_stack_size_counts_as_one():
    priced = price_from_index({"typeLine": "Exalted Orb", "frameType": 5}, 0, INDEX)
    assert priced.stack == 1
    assert priced.total_ex == 1.0


def test_prices_a_unique_by_name():
    priced = price_from_index({"name": "Mageblood", "frameType": 3}, 1, INDEX)
    assert priced.item_class is ItemClass.UNIQUE
    assert priced.total_ex == 135416.55
    assert priced.quantity == 5808


def test_prices_a_lineage_support_gem():
    item = {"typeLine": "Uul-Netol's Embrace", "frameType": 4}
    priced = price_from_index(item, 2, INDEX)
    assert priced.item_class is ItemClass.GEM
    assert priced.total_ex == 150826.1


def test_gem_level_is_part_of_the_lookup_key():
    item = {"typeLine": "Uncut Skill Gem", "frameType": 4,
            "properties": [{"name": "Level", "values": [["20", 0]]}]}
    assert gem_index_key(item) == "Uncut Skill Gem (Level 20)"
    assert price_from_index(item, 0, INDEX).total_ex == 1595.5


def test_item_absent_from_index_is_tagged_not_zeroed():
    priced = price_from_index({"name": "Nonesuch", "frameType": 3}, 0, INDEX)
    assert priced.total_ex is None
    assert priced.tag == "unpriced:no-index"


def test_unknown_class_is_tagged():
    priced = price_from_index({"typeLine": "Ornate Wombgift"}, 0, INDEX)
    assert priced.item_class is ItemClass.UNKNOWN
    assert priced.tag == "unpriced:unknown-class"
    assert priced.total_ex is None


def test_gear_is_not_index_priced():
    priced = price_from_index({"typeLine": "Vaal Greaves", "frameType": 2}, 0, INDEX)
    assert priced.total_ex is None
    assert priced.tag == "unpriced:no-index"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_index_pricer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sox.valuation.index_pricer'`

- [ ] **Step 3: Implement `src/sox/valuation/index_pricer.py`**

```python
"""Index pricing — the free path. No API calls happen here."""

from __future__ import annotations

from dataclasses import dataclass

from sox.scout import IndexEntry
from sox.valuation.classify import ItemClass, classify, display_name

# Classes the index can price at all.
INDEXABLE = (ItemClass.CURRENCY, ItemClass.GEM, ItemClass.UNIQUE)


@dataclass(frozen=True)
class PricedItem:
    name: str
    item_class: ItemClass
    unit_price_ex: float | None
    stack: int
    total_ex: float | None
    source: str
    tag: str | None
    tab: int
    quantity: int = 0     # index listing count — a liquidity signal
    spread: float = 1.0   # max/min ratio across the item's rolled ranges


def _level_of(item: dict) -> int | None:
    for prop in item.get("properties") or []:
        if prop.get("name") == "Level":
            values = prop.get("values") or []
            if values and values[0]:
                try:
                    return int(str(values[0][0]).split()[0])
                except (ValueError, IndexError):
                    return None
    return None


def gem_index_key(item: dict) -> str:
    """Gems price by level: an L20 uncut gem is ~8x an L4."""
    name = display_name(item)
    level = _level_of(item)
    return f"{name} (Level {level})" if level is not None else name


def price_from_index(item: dict, tab: int, index: dict[str, IndexEntry]) -> PricedItem:
    item_class = classify(item)
    name = display_name(item)
    stack = int(item.get("stackSize") or 1)

    if item_class is ItemClass.UNKNOWN:
        return PricedItem(name, item_class, None, stack, None, "unpriced",
                          "unpriced:unknown-class", tab)

    if item_class not in INDEXABLE:
        # Gear and endgame items are priced by search, not here.
        return PricedItem(name, item_class, None, stack, None, "unpriced",
                          "unpriced:no-index", tab)

    key = gem_index_key(item) if item_class is ItemClass.GEM else name
    entry = index.get(key) or index.get(name)
    if entry is None:
        return PricedItem(name, item_class, None, stack, None, "unpriced",
                          "unpriced:no-index", tab)

    return PricedItem(
        name=name,
        item_class=item_class,
        unit_price_ex=entry.price_ex,
        stack=stack,
        total_ex=entry.price_ex * stack,
        source="index",
        tag=None,
        tab=tab,
        quantity=entry.quantity,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_index_pricer.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/sox/valuation/index_pricer.py tests/test_index_pricer.py
git commit -m "feat(index-pricer): index lookup with stack and gem-level keys"
```

---

## Task 10: Report and snapshot

**Files:**
- Create: `src/sox/report.py`
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: `PricedItem` (Task 9).
- Produces: `Totals(total_ex: float, by_tab: dict[int, float], unpriced: int)`, `summarize(items: list[PricedItem]) -> Totals`, `render(items, totals, divine_ratio, top_n=20) -> str`, `write_snapshot(items, totals, league, divine_ratio, directory: Path, timestamp: str) -> Path`.

`timestamp` is passed in rather than generated, so snapshot writing is deterministic under test.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_report.py
import json

from sox.report import render, summarize, write_snapshot
from sox.valuation.classify import ItemClass
from sox.valuation.index_pricer import PricedItem


def priced(name, total, tab=0, tag=None, klass=ItemClass.CURRENCY):
    return PricedItem(name, klass, total, 1, total, "index" if total else "unpriced", tag, tab)


ITEMS = [
    priced("Mageblood", 135416.55, tab=1, klass=ItemClass.UNIQUE),
    priced("Exalted Orb", 412.0, tab=0),
    priced("Ornate Wombgift", None, tab=2, tag="unpriced:unknown-class",
           klass=ItemClass.UNKNOWN),
]


def test_totals_sum_priced_items_only():
    totals = summarize(ITEMS)
    assert totals.total_ex == 135828.55
    assert totals.by_tab == {1: 135416.55, 0: 412.0, 2: 0.0}
    assert totals.unpriced == 1


def test_render_shows_divine_conversion_and_top_items():
    text = render(ITEMS, summarize(ITEMS), divine_ratio=336.5)
    assert "Mageblood" in text
    assert "div" in text.lower()
    assert "403" in text or "402" in text     # 135416.55 / 336.5 ~= 402.4


def test_render_surfaces_unpriced_items_rather_than_hiding_them():
    text = render(ITEMS, summarize(ITEMS), divine_ratio=336.5)
    assert "unpriced" in text.lower()
    assert "Ornate Wombgift" in text


def test_snapshot_round_trips(tmp_path):
    totals = summarize(ITEMS)
    path = write_snapshot(ITEMS, totals, "Runes of Aldur", 336.5, tmp_path, "2026-08-17T21-30")
    assert path.name == "2026-08-17T21-30.json"
    data = json.loads(path.read_text())
    assert data["league"] == "Runes of Aldur"
    assert data["totals"]["total_ex"] == 135828.55
    assert len(data["items"]) == 3


def test_snapshot_never_contains_a_session_token(tmp_path):
    totals = summarize(ITEMS)
    path = write_snapshot(ITEMS, totals, "L", 336.5, tmp_path, "t")
    assert "POESESSID" not in path.read_text()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_report.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sox.report'`

- [ ] **Step 3: Implement `src/sox/report.py`**

```python
"""Terminal report and JSON snapshot.

Unpriced items are shown, never dropped: a quiet omission reads as "worth
nothing", which is exactly the wrong impression.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from sox.valuation.index_pricer import PricedItem


@dataclass(frozen=True)
class Totals:
    total_ex: float
    by_tab: dict[int, float]
    unpriced: int


def summarize(items: list[PricedItem]) -> Totals:
    by_tab: dict[int, float] = {}
    total = 0.0
    unpriced = 0
    for item in items:
        by_tab.setdefault(item.tab, 0.0)
        if item.total_ex is None:
            unpriced += 1
            continue
        by_tab[item.tab] += item.total_ex
        total += item.total_ex
    return Totals(round(total, 2), by_tab, unpriced)


def _fmt(ex: float, divine_ratio: float) -> str:
    if divine_ratio > 0:
        return f"{ex:,.0f} ex ({ex / divine_ratio:,.1f} div)"
    return f"{ex:,.0f} ex"


def render(items: list[PricedItem], totals: Totals, divine_ratio: float, top_n: int = 20) -> str:
    lines = [f"Stash total: {_fmt(totals.total_ex, divine_ratio)}", ""]

    lines.append("By tab:")
    for tab, value in sorted(totals.by_tab.items(), key=lambda kv: -kv[1]):
        lines.append(f"  tab {tab:>3}  {_fmt(value, divine_ratio)}")
    lines.append("")

    ranked = sorted(
        (i for i in items if i.total_ex is not None),
        key=lambda i: -i.total_ex,
    )[:top_n]
    lines.append(f"Top {len(ranked)} items:")
    for item in ranked:
        stack = f" x{item.stack}" if item.stack > 1 else ""
        lines.append(f"  {_fmt(item.total_ex, divine_ratio):>28}  {item.name}{stack}")

    unpriced = [i for i in items if i.total_ex is None]
    if unpriced:
        lines += ["", f"Unpriced ({len(unpriced)}) — these are NOT worth zero:"]
        for item in unpriced[:top_n]:
            lines.append(f"  {item.tag or 'unpriced'}  {item.name}  (tab {item.tab})")
    return "\n".join(lines)


def write_snapshot(
    items: list[PricedItem],
    totals: Totals,
    league: str,
    divine_ratio: float,
    directory: Path,
    timestamp: str,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{timestamp}.json"
    payload = {
        "league": league,
        "timestamp": timestamp,
        "divine_ratio_ex": divine_ratio,
        "totals": {
            "total_ex": totals.total_ex,
            "by_tab": {str(k): v for k, v in totals.by_tab.items()},
            "unpriced": totals.unpriced,
        },
        "items": [
            {**asdict(item), "item_class": str(item.item_class)} for item in items
        ],
    }
    path.write_text(json.dumps(payload, indent=2))
    return path
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_report.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/sox/report.py tests/test_report.py
git commit -m "feat(report): ranked table, tab totals, and JSON snapshot"
```

---

## Task 11: CLI — `sox tabs` and `sox value` (Milestone 1)

**Files:**
- Create: `src/sox/cli.py`
- Create: `src/sox/pipeline.py`
- Test: `tests/test_pipeline.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: everything from Tasks 1–10.
- Produces: `value_stash(session, scout, cfg, account, league, tabs) -> tuple[list[PricedItem], League]`, `main(argv: list[str] | None = None) -> int`.

After this task the tool runs end to end with zero trade-API calls.

- [ ] **Step 1: Write the failing pipeline test**

```python
# tests/test_pipeline.py
import httpx

from sox.cache import Cache
from sox.config import Config
from sox.ggg.governor import RateGovernor
from sox.ggg.session import GGGSession
from sox.pipeline import value_stash
from sox.scout import ScoutClient

LEAGUES = [{"Value": "Runes of Aldur", "ShortName": "runes", "IsCurrent": True,
            "DivinePrice": 336.5, "BaseCurrencyText": "Exalted Orb"}]

TABS = {"tabs": [{"n": "Currency", "i": 0, "id": "a", "type": "CurrencyStash"},
                 {"n": "Gear", "i": 1, "id": "b", "type": "NormalStash"}]}

TAB_ITEMS = {
    0: {"items": [{"typeLine": "Exalted Orb", "frameType": 5, "stackSize": 100}]},
    1: {"items": [{"name": "Mageblood", "typeLine": "Utility Belt", "frameType": 3}]},
}


def ggg_handler(request):
    if request.url.params.get("tabs") == "1":
        return httpx.Response(200, json=TABS)
    index = int(request.url.params.get("tabIndex"))
    return httpx.Response(200, json=TAB_ITEMS[index])


def scout_handler(request):
    if request.url.path.endswith("/Leagues"):
        return httpx.Response(200, json=LEAGUES)
    category = request.url.params.get("category")
    if category == "currency":
        return httpx.Response(200, json={"Items": [
            {"Name": "Exalted Orb", "Text": "Exalted Orb", "CurrentPrice": 1.0,
             "CurrentQuantity": 1, "ItemMetadata": {}}]})
    if category == "accessory":
        return httpx.Response(200, json={"Items": [
            {"Name": "Mageblood", "Text": "Mageblood", "CurrentPrice": 135416.55,
             "CurrentQuantity": 5808, "ItemMetadata": {}}]})
    return httpx.Response(200, json={"Items": []})


def build(tmp_path):
    gov = RateGovernor(clock=lambda: 0.0, sleeper=lambda s: None)
    session = GGGSession("s", gov, httpx.Client(transport=httpx.MockTransport(ggg_handler)), "t")
    scout = ScoutClient(
        httpx.Client(transport=httpx.MockTransport(scout_handler)),
        Cache(tmp_path / "c.sqlite"), "t",
    )
    return session, scout


def test_values_every_tab(tmp_path):
    session, scout = build(tmp_path)
    items, league = value_stash(session, scout, Config(), account="me",
                                league="Runes of Aldur", tabs=None)
    assert league.divine_price_ex == 336.5
    by_name = {i.name: i for i in items}
    assert by_name["Exalted Orb"].total_ex == 100.0
    assert by_name["Mageblood"].total_ex == 135416.55


def test_tab_selection_limits_reads(tmp_path):
    session, scout = build(tmp_path)
    items, _ = value_stash(session, scout, Config(), account="me",
                           league="Runes of Aldur", tabs=[1])
    assert [i.name for i in items] == ["Mageblood"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sox.pipeline'`

- [ ] **Step 3: Implement `src/sox/pipeline.py`**

```python
"""Wires the pieces into one valuation run."""

from __future__ import annotations

from sox.config import Config
from sox.ggg import stash
from sox.ggg.session import GGGSession
from sox.scout import League, ScoutClient
from sox.valuation.index_pricer import PricedItem, price_from_index


def value_stash(
    session: GGGSession,
    scout: ScoutClient,
    cfg: Config,
    account: str,
    league: str,
    tabs: list[int] | None,
) -> tuple[list[PricedItem], League]:
    league_info = scout.current_league()
    index = scout.prices(league_info.short)

    available = stash.list_tabs(session, account, league)
    selected = [t for t in available if tabs is None or t.index in tabs]

    priced: list[PricedItem] = []
    for tab in selected:
        for item in stash.fetch_tab(session, account, league, tab.index):
            priced.append(price_from_index(item, tab.index, index))
    return priced, league_info
```

- [ ] **Step 4: Run pipeline tests to verify they pass**

Run: `uv run pytest tests/test_pipeline.py -v`
Expected: 2 passed

- [ ] **Step 5: Write the failing CLI test**

```python
# tests/test_cli.py
import pytest

from sox.cli import build_parser, main


def test_parser_supports_tabs_and_value():
    parser = build_parser()
    assert parser.parse_args(["tabs"]).command == "tabs"
    args = parser.parse_args(["value", "--tab", "1", "--tab", "3"])
    assert args.tab == [1, 3]


def test_value_accepts_no_trade_flag():
    args = build_parser().parse_args(["value", "--no-trade"])
    assert args.no_trade is True


def test_missing_account_exits_with_a_clear_message(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("POESESSID", "x")
    code = main(["value", "--config", str(tmp_path / "missing.toml")])
    assert code == 2
    assert "account" in capsys.readouterr().err.lower()


def test_missing_secret_exits_without_traceback(capsys, tmp_path, monkeypatch):
    monkeypatch.delenv("POESESSID", raising=False)
    config = tmp_path / "c.toml"
    config.write_text('account = "me"\n')
    monkeypatch.setattr("sox.secrets.DEFAULT_SECRETS_PATH", tmp_path / "nope")
    code = main(["value", "--config", str(config)])
    assert code == 2
    assert "POESESSID" in capsys.readouterr().err
```

- [ ] **Step 6: Run CLI test to verify it fails**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sox.cli'`

- [ ] **Step 7: Implement `src/sox/cli.py`**

```python
"""Command line entry point."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

from sox import report
from sox.cache import Cache
from sox.config import load_config
from sox.ggg import stash
from sox.ggg.governor import RateGovernor
from sox.ggg.session import GGGError, GGGSession
from sox.pipeline import value_stash
from sox.scout import ScoutClient
from sox.secrets import SecretError, load_poesessid


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sox", description="PoE2 stash valuator")
    parser.add_argument("--config", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("tabs", help="list stash tabs")

    value = sub.add_parser("value", help="price the stash")
    value.add_argument("--tab", type=int, action="append", help="repeatable; default all")
    value.add_argument("--no-trade", action="store_true",
                       help="index pricing only; make no trade API calls")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args.config)

    if not cfg.account:
        print("error: no account configured. Set `account = \"...\"` in "
              "~/.config/sox/config.toml", file=sys.stderr)
        return 2

    try:
        poesessid = load_poesessid()
    except SecretError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    cache = Cache(cfg.cache_path)
    governor = RateGovernor()
    session = GGGSession(poesessid, governor, httpx.Client(timeout=30), cfg.user_agent)
    scout = ScoutClient(httpx.Client(timeout=30), cache, cfg.user_agent)

    try:
        league = cfg.league or scout.current_league().value
        if args.command == "tabs":
            for tab in stash.list_tabs(session, cfg.account, league):
                print(f"{tab.index:>3}  {tab.type:<16} {tab.name}")
            return 0

        tabs = args.tab or cfg.tabs
        items, league_info = value_stash(session, scout, cfg, cfg.account, league, tabs)
        totals = report.summarize(items)
        print(report.render(items, totals, league_info.divine_price_ex))

        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        path = report.write_snapshot(items, totals, league,
                                     league_info.divine_price_ex,
                                     cfg.snapshot_dir, timestamp)
        print(f"\nsnapshot: {path}")
        return 0
    except (GGGError, SecretError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        cache.close()


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 8: Run the whole suite**

Run: `uv run pytest -q`
Expected: all green (≈55 tests)

- [ ] **Step 9: Commit**

```bash
git add src/sox/cli.py src/sox/pipeline.py tests/test_pipeline.py tests/test_cli.py
git commit -m "feat(cli): sox tabs and index-only sox value"
```

- [ ] **Step 10: Smoke test against the real account**

Run: `uv run sox tabs`, then `uv run sox value --no-trade`

Expected: a tab list, then a total with currency/gems/uniques priced and gear listed as `unpriced:no-index`. If a tab raises `StashParseError`, that is Task 6 Step 6's follow-up — capture the shape and extend the parser.

**Milestone 1 complete.**

---

## Task 12: Roll parsing and scoring

**Files:**
- Create: `src/sox/valuation/rolls.py`
- Test: `tests/test_rolls.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `parse_ranges(mod_text: str) -> list[tuple[float, float]]`, `parse_values(mod_text: str) -> list[float]`, `spread_of(metadata: dict) -> float`, `roll_score(item_mods: list[str], metadata: dict) -> float | None`.

`roll_score` returns a 0.0–1.0 percentile, or `None` when nothing can be compared. The `metadata` shape is poe2scout's `ItemMetadata`, whose mods are usually strings but are occasionally structured dicts (4 of 1,949 observed) — both must be handled.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_rolls.py
from sox.valuation.rolls import parse_ranges, parse_values, roll_score, spread_of

VENTORS = {"explicit_mods": [
    "+(5-15)% to Fire Resistance",
    "+(5-15)% to Cold Resistance",
    "(10-20)% increased Rarity of Items found",
]}


def test_parses_ranges():
    assert parse_ranges("+(17-23)% to Chaos Resistance") == [(17.0, 23.0)]
    assert parse_ranges("Adds (3-5) to (8-12) Physical Damage") == [(3.0, 5.0), (8.0, 12.0)]
    assert parse_ranges("Cannot be Frozen") == []


def test_parses_actual_values():
    assert parse_values("+21% to Chaos Resistance") == [21.0]
    assert parse_values("Adds 4 to 10 Physical Damage") == [4.0, 10.0]


def test_spread_is_the_widest_ratio():
    assert spread_of({"explicit_mods": ["+(5-15)% to Fire Resistance"]}) == 3.0
    assert spread_of({"explicit_mods": ["Cannot be Frozen"]}) == 1.0


def test_zero_floor_range_counts_as_maximally_swingy():
    """Ventor's rolls "+(0-80) to maximum Life" — the widest swing there is.

    The ratio is undefined, so it must score high rather than be skipped.
    Skipping it made the metric blind to the case it exists to catch.
    """
    from sox.valuation.rolls import ZERO_FLOOR_SPREAD
    assert spread_of({"explicit_mods": ["+(0-80) to maximum Life"]}) == ZERO_FLOOR_SPREAD
    assert ZERO_FLOOR_SPREAD >= 10.0


def test_spread_handles_structured_mods():
    """A few scout entries carry mods as dicts rather than strings."""
    metadata = {"explicit_mods": [
        {"mods": [{"magnitudes": [{"min": "5", "max": "20"}]}]},
    ]}
    assert spread_of(metadata) == 4.0


def test_perfect_rolls_score_one():
    assert roll_score(["+15% to Fire Resistance", "+15% to Cold Resistance",
                       "20% increased Rarity of Items found"], VENTORS) == 1.0


def test_floor_rolls_score_zero():
    assert roll_score(["+5% to Fire Resistance", "+5% to Cold Resistance",
                       "10% increased Rarity of Items found"], VENTORS) == 0.0


def test_mid_rolls_score_between():
    score = roll_score(["+10% to Fire Resistance", "+10% to Cold Resistance",
                        "15% increased Rarity of Items found"], VENTORS)
    assert 0.4 < score < 0.6


def test_returns_none_when_nothing_comparable():
    assert roll_score(["Cannot be Frozen"], {"explicit_mods": ["Cannot be Frozen"]}) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_rolls.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sox.valuation.rolls'`

- [ ] **Step 3: Implement `src/sox/valuation/rolls.py`**

```python
"""Roll ranges and how good our copy is within them.

This is what makes a unique's index price usable. The index reports one
number, which for a wide-rolling unique is the floor: Ventor's Gamble indexes
at ~7ex across 26,747 listings while a good one sells for many Divine.
"""

from __future__ import annotations

import re

RANGE = re.compile(r"\((\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\)")
NUMBER = re.compile(r"(\d+(?:\.\d+)?)")

MOD_KEYS = ("explicit_mods", "implicit_mods")

# Score for a range whose floor is 0, e.g. "+(0-80) to maximum Life". The
# max/min ratio is undefined but the swing is total, so it must rank high.
ZERO_FLOOR_SPREAD = 10.0


def parse_ranges(mod_text: str) -> list[tuple[float, float]]:
    return [(float(lo), float(hi)) for lo, hi in RANGE.findall(mod_text)]


def parse_values(mod_text: str) -> list[float]:
    return [float(n) for n in NUMBER.findall(mod_text)]


def _ranges_from_structured(mod: dict) -> list[tuple[float, float]]:
    out = []
    for sub in mod.get("mods") or []:
        for magnitude in sub.get("magnitudes") or []:
            try:
                lo, hi = float(magnitude["min"]), float(magnitude["max"])
            except (KeyError, TypeError, ValueError):
                continue
            out.append((lo, hi))
    return out


def _all_ranges(metadata: dict) -> list[tuple[float, float]]:
    ranges: list[tuple[float, float]] = []
    for key in MOD_KEYS:
        for mod in metadata.get(key) or []:
            if isinstance(mod, dict):
                ranges.extend(_ranges_from_structured(mod))
            else:
                ranges.extend(parse_ranges(mod))
    return ranges


def spread_of(metadata: dict) -> float:
    """Widest max/min ratio across the item's rolled ranges.

    High spread means the index price cannot describe a specific copy. It is
    NOT on its own a reason to spend a search — Thunderfist spreads x111 and
    sells for ~3ex, so even a perfect copy is worth ~3ex.
    """
    ratios = []
    for lo, hi in _all_ranges(metadata):
        if hi <= lo:
            continue
        ratios.append(hi / lo if lo > 0 else ZERO_FLOOR_SPREAD)
    return max(ratios) if ratios else 1.0


def roll_score(item_mods: list[str], metadata: dict) -> float | None:
    """Mean percentile of our values within the ranges the unique can roll."""
    templates = []
    for key in MOD_KEYS:
        for mod in metadata.get(key) or []:
            if isinstance(mod, dict):
                templates.extend(_ranges_from_structured(mod))
            else:
                ranges = parse_ranges(mod)
                if ranges:
                    templates.append(ranges[0])

    values = []
    for text in item_mods:
        found = parse_values(text)
        if found:
            values.append(found[0])

    percentiles = []
    for (lo, hi), value in zip(templates, values):
        if hi <= lo:
            continue
        percentiles.append(min(max((value - lo) / (hi - lo), 0.0), 1.0))

    if not percentiles:
        return None
    return sum(percentiles) / len(percentiles)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_rolls.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/sox/valuation/rolls.py tests/test_rolls.py
git commit -m "feat(rolls): parse roll ranges and score a copy within them"
```

---

## Task 13: Allowlist loading and mod mapping

**Files:**
- Create: `src/sox/valuation/allowlists.py`
- Create: `src/sox/valuation/mods.py`
- Test: `tests/test_allowlists.py`
- Test: `tests/test_mods.py`

**Interfaces:**
- Consumes: the three generated files in `src/sox/data/`.
- Produces:
  - `allowlists.py`: `ModEntry(ids: list[str], slug: str, text: str, weight: int, category: str, ambiguous: bool)`, `load_mods() -> list[ModEntry]`, `load_bases() -> BaseRules`, `load_uniques() -> UniqueRules`; `BaseRules(ilvl_tiers, slots, named, avoid, rune_prefixes)`, `UniqueRules(thresholds: dict, entries: dict[str, int])`.
  - `mods.py`: `normalize_mod(text: str) -> str`, `match_mod(text: str, entries: list[ModEntry]) -> ModEntry | None`, `score_mods(item_mods: list[str], entries) -> tuple[int, dict[str, int]]`.

`normalize_mod` replaces every number with `#` and folds case/whitespace/leading `+`, matching the generator's normalization so a mod written one way in the stash still matches the allowlist.

- [ ] **Step 1: Write the failing allowlist test**

```python
# tests/test_allowlists.py
from sox.valuation.allowlists import load_bases, load_mods, load_uniques


def test_loads_every_mod_with_ids_and_weights():
    mods = load_mods()
    assert len(mods) == 92
    assert all(entry.ids for entry in mods)
    assert {entry.weight for entry in mods} == {1, 2, 3}


def test_mods_carry_archetype_tags():
    by_text = {m.text: m for m in load_mods()}
    assert "attack" in by_text["# to Level of all Melee Skills"].tags
    assert "spell" in by_text["#% increased Cast Speed"].tags
    # A minion mod serves minion buyers only, never attack/spell.
    minion = by_text["Minions have #% increased Attack and Cast Speed"]
    assert minion.tags == ["minion"]
    assert all(m.tags for m in load_mods()), "every mod must carry at least one tag"


def test_ambiguous_mods_keep_all_ids():
    spirit = [m for m in load_mods() if m.slug == "to_spirit"][0]
    assert spirit.ambiguous is True
    assert len(spirit.ids) == 2


def test_base_rules_carry_ilvl_tiers_and_slots():
    rules = load_bases()
    assert rules.ilvl_tiers[0] == (82, 3)
    assert "armour.chest" in rules.slots
    assert "weapon.warstaff" in rules.slots
    assert "Dreaming Quarterstaff" in rules.avoid
    assert set(rules.rune_prefixes) == {"Runeforged", "Runemastered"}


def test_unique_rules_carry_thresholds_and_named_entries():
    rules = load_uniques()
    assert rules.thresholds["chase_price_ex"] == 5000
    assert rules.thresholds["swing_ratio"] == 2.0
    assert rules.entries["Mageblood"] == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_allowlists.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sox.valuation.allowlists'`

- [ ] **Step 3: Implement `src/sox/valuation/allowlists.py`**

```python
"""Load the generated data files.

These files are produced by scripts/resolve_*.py against GGG's live tables.
Do not hand-edit them; regenerate instead.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"


@dataclass(frozen=True)
class ModEntry:
    ids: list[str]
    slug: str
    text: str
    weight: int
    category: str
    tags: list[str] = field(default_factory=list)   # archetypes this mod serves
    ambiguous: bool = False


@dataclass(frozen=True)
class BaseRules:
    ilvl_tiers: list[tuple[int, int]]        # (min ilvl, weight), highest first
    slots: dict[str, int]                    # trade category -> weight
    named: dict[str, int]                    # base name -> weight
    avoid: set[str] = field(default_factory=set)
    rune_prefixes: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class UniqueRules:
    thresholds: dict[str, float]
    entries: dict[str, int]                  # unique name -> weight


def _read(name: str) -> dict:
    with (DATA / name).open("rb") as fh:
        return tomllib.load(fh)


def load_mods() -> list[ModEntry]:
    raw = _read("mod_allowlist.toml")
    entries = []
    for category in raw.get("category", []):
        for mod in category.get("mod", []):
            entries.append(ModEntry(
                ids=list(mod["ids"]),
                slug=mod["slug"],
                text=mod["text"],
                weight=int(mod["weight"]),
                category=category["name"],
                tags=list(mod.get("tags", [])),
                ambiguous=bool(mod.get("ambiguous", False)),
            ))
    return entries


def load_bases() -> BaseRules:
    raw = _read("base_allowlist.toml")
    tiers = sorted(
        ((int(t["min"]), int(t["weight"])) for t in raw.get("ilvl_tier", [])),
        key=lambda pair: -pair[0],
    )
    return BaseRules(
        ilvl_tiers=tiers,
        slots={s["category"]: int(s["weight"]) for s in raw.get("slot", [])},
        named={b["name"]: int(b["weight"]) for b in raw.get("named_base", [])},
        avoid={b["name"] for b in raw.get("avoid_base", [])},
        rune_prefixes={f["prefix"]: int(f["bonus_weight"]) for f in raw.get("rune_family", [])},
    )


def load_uniques() -> UniqueRules:
    raw = _read("unique_allowlist.toml")
    return UniqueRules(
        thresholds={k: float(v) for k, v in (raw.get("thresholds") or {}).items()},
        entries={u["name"]: int(u["weight"]) for u in raw.get("unique", [])},
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_allowlists.py -v`
Expected: 4 passed

- [ ] **Step 5: Write the failing mod-matching test**

```python
# tests/test_mods.py
from sox.valuation.allowlists import load_mods
from sox.valuation.mods import match_mod, normalize_mod, score_mods

MODS = load_mods()


def test_normalize_replaces_numbers_with_hash():
    assert normalize_mod("+96 to maximum Life") == "# to maximum life"
    assert normalize_mod("Adds 4 to 10 Physical Damage") == "adds # to # physical damage"


def test_normalize_folds_the_capitalization_traps():
    """GGG writes Physical "Damage to Attacks" but Fire "damage to Attacks"."""
    assert normalize_mod("Adds 1 to 2 Fire Damage to Attacks") == \
           normalize_mod("Adds 1 to 2 fire damage to attacks")


def test_matches_a_real_stash_mod_to_its_allowlist_entry():
    entry = match_mod("+96 to maximum Life", MODS)
    assert entry is not None
    assert entry.ids == ["explicit.stat_3299347043"]
    assert entry.weight == 3


def test_unknown_mod_returns_none_rather_than_guessing():
    assert match_mod("+3 to Level of all Interpretive Dance Skills", MODS) is None


def test_score_sums_weights_and_reports_categories():
    total, by_category = score_mods([
        "+96 to maximum Life",              # weight 3, defence_core
        "+35% increased Movement Speed",    # weight 3, utility
        "+12% to Fire Resistance",          # weight 1, resistances
    ], MODS)
    assert total == 7
    assert by_category["defence_core"] == 3
    assert by_category["resistances"] == 1


def test_unmatched_mods_contribute_nothing():
    total, _ = score_mods(["Cannot be Frozen"], MODS)
    assert total == 0


def test_supporting_mods_do_not_stack_without_limit():
    """Four low-tier mods make an item worse, not better — they block crafting."""
    from sox.valuation.mods import SUPPORTING_CAP
    total, _ = score_mods([
        "+12% to Fire Resistance", "+11% to Cold Resistance",
        "+14% to Lightning Resistance", "+9% to Fire Resistance",
    ], MODS)
    assert total == SUPPORTING_CAP


def test_cap_applies_only_to_supporting_mods():
    total, _ = score_mods([
        "+96 to maximum Life",              # weight 3, uncapped
        "+35% increased Movement Speed",    # weight 3, uncapped
        "+12% to Fire Resistance", "+11% to Cold Resistance",
        "+14% to Lightning Resistance",     # 3 supporting, capped to 2
    ], MODS)
    assert total == 3 + 3 + 2
```

- [ ] **Step 6: Run test to verify it fails**

Run: `uv run pytest tests/test_mods.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sox.valuation.mods'`

- [ ] **Step 7: Implement `src/sox/valuation/mods.py`**

```python
"""Map a stash item's mod text onto allowlist entries.

Normalization mirrors scripts/resolve_allowlist.py so that a mod matches
regardless of case, spacing, or a leading plus. An unmatched mod is skipped —
the tool never guesses a stat id, because a wrong id silently skews the
search built from it.
"""

from __future__ import annotations

import re

from sox.valuation.allowlists import ModEntry

_NUMBER = re.compile(r"[+-]?\d+(?:\.\d+)?")

# Most a pile of weight-1 mods may contribute in total.
SUPPORTING_CAP = 2

# Ceiling on the coherence bonus, so a deep stack cannot dominate the score.
MAX_COHERENCE_BONUS = 3


def normalize_mod(text: str) -> str:
    text = _NUMBER.sub("#", text)
    text = text.casefold()
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(?<![a-z0-9])\+(?=#)", "", text)
    return text


def match_mod(text: str, entries: list[ModEntry]) -> ModEntry | None:
    target = normalize_mod(text)
    for entry in entries:
        if normalize_mod(entry.text) == target:
            return entry
    return None


def coherence_bonus(item_mods: list[str], entries: list[ModEntry]) -> tuple[int, str]:
    """Reward many mods serving ONE archetype.

    Counted over archetype tags, not allowlist categories. A real build's mods
    span categories (projectile levels + attack speed + flat damage is a bow
    item), while a single category can hold mods for two unrelated builds
    (+Melee Skills and +Spell Skills are both skill_levels and share no buyer).
    """
    counts: dict[str, int] = {}
    for text in item_mods:
        entry = match_mod(text, entries)
        if entry is None:
            continue
        for tag in entry.tags:
            counts[tag] = counts.get(tag, 0) + 1
    if not counts:
        return 0, ""
    tag, top = max(counts.items(), key=lambda kv: kv[1])
    bonus = min(max(top - 1, 0), MAX_COHERENCE_BONUS)
    return bonus, (f"{tag}x{top}" if bonus else "")


def score_mods(item_mods: list[str], entries: list[ModEntry]) -> tuple[int, dict[str, int]]:
    """Sum allowlist weights, capping what supporting mods can contribute.

    Weight-1 mods are "supporting; only matters in combination", so they must
    not stack without limit. Community pricing guidance is explicit that an
    item with four or more low-tier mods is worth LESS, not more — those mods
    occupy affix slots a buyer would otherwise craft into. Without this cap, a
    pile of junk resistances out-scores the open slots it consumed.
    """
    total = 0
    supporting = 0
    by_category: dict[str, int] = {}
    for text in item_mods:
        entry = match_mod(text, entries)
        if entry is None:
            continue
        weight = entry.weight
        if weight == 1:
            if supporting >= SUPPORTING_CAP:
                continue
            supporting += weight
        total += weight
        by_category[entry.category] = by_category.get(entry.category, 0) + weight
    return total, by_category
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/test_mods.py -v`
Expected: 6 passed

- [ ] **Step 9: Commit**

```bash
git add src/sox/valuation/allowlists.py src/sox/valuation/mods.py tests/test_allowlists.py tests/test_mods.py
git commit -m "feat(mods): load allowlists and score item mods without guessing ids"
```

---

## Task 14: Candidate selection

**Files:**
- Create: `src/sox/valuation/candidates.py`
- Test: `tests/test_candidates.py`

**Interfaces:**
- Consumes: `ModEntry`/`BaseRules`/`UniqueRules` (Task 13), `score_mods` (Task 13), `roll_score`/`spread_of` (Task 12), `ItemClass`/`rarity_of` (Task 8).
- Produces: `Candidate(item: dict, tab: int, item_class: ItemClass, score: int, reason: str)`, `score_gear(item, mods, base_rules) -> tuple[int, str]`, `used_affixes(item) -> int`, `open_affix_bonus(item, mod_score, has_premium) -> tuple[int, str]`, `AFFIX_CAPACITY: dict[Rarity, int]`, `should_search_unique(item, entry: IndexEntry | None, rules: UniqueRules) -> str | None`, `select(items: list[tuple[dict, int]], index, cfg_budgets, mods, base_rules, unique_rules) -> list[Candidate]`.

`reason` records *why* an item qualified, so the report can explain the spend.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_candidates.py
from sox.config import Budgets
from sox.scout import IndexEntry
from sox.valuation.allowlists import load_bases, load_mods, load_uniques
from sox.valuation.candidates import score_gear, select, should_search_unique

MODS = load_mods()
BASES = load_bases()
UNIQUES = load_uniques()


def rare(mods, ilvl=82, base="Vaal Greaves"):
    return {"typeLine": base, "baseType": base, "frameType": 2, "ilvl": ilvl,
            "explicitMods": mods}


def test_three_strong_mods_qualify():
    score, reason = score_gear(
        rare(["+96 to maximum Life", "+35% increased Movement Speed",
              "+40% increased Critical Hit Chance"]), MODS, BASES)
    assert score >= 6
    assert "mods" in reason


def test_a_pile_of_resistances_does_not_qualify():
    """Weight-1 mods alone are a vendor item, by design."""
    score, _ = score_gear(
        rare(["+12% to Fire Resistance", "+11% to Cold Resistance",
              "+14% to Lightning Resistance"]), MODS, BASES)
    assert score < 6


def test_open_affixes_beat_the_same_mods_with_junk_filling_them():
    """Two strong mods plus room to craft is worth more than a finished item."""
    craftable = rare(["+96 to maximum Life", "+40% increased Critical Hit Chance"])
    filled = rare(["+96 to maximum Life", "+40% increased Critical Hit Chance",
                   "+12% to Fire Resistance", "+11% to Cold Resistance",
                   "+3% to Lightning Resistance", "+2% to Chaos Resistance"])
    open_score, open_reason = score_gear(craftable, MODS, BASES)
    filled_score, _ = score_gear(filled, MODS, BASES)
    assert open_score > filled_score
    assert "open" in open_reason


def test_corrupted_item_gets_no_open_affix_bonus():
    """A corrupted item cannot be crafted, so its empty slots stay empty."""
    item = rare(["+96 to maximum Life", "+40% increased Critical Hit Chance"])
    corrupted = {**item, "corrupted": True}
    assert score_gear(corrupted, MODS, BASES)[0] < score_gear(item, MODS, BASES)[0]


def test_mirrored_item_gets_no_open_affix_bonus():
    item = rare(["+96 to maximum Life", "+40% increased Critical Hit Chance"])
    mirrored = {**item, "mirrored": True}
    assert score_gear(mirrored, MODS, BASES)[0] < score_gear(item, MODS, BASES)[0]


def test_blank_rare_with_open_slots_gets_no_bonus():
    """Open slots only matter once something worth keeping is already on it."""
    score, reason = score_gear(rare(["+2% to Chaos Resistance"]), MODS, BASES)
    assert "open" not in reason


def test_fractured_mod_counts_toward_used_affixes():
    item = {"typeLine": "Vaal Greaves", "baseType": "Vaal Greaves", "frameType": 2,
            "ilvl": 82, "explicitMods": ["+96 to maximum Life"],
            "fracturedMods": ["+35% increased Movement Speed"]}
    score, reason = score_gear(item, MODS, BASES)
    assert "open4" in reason, "6 capacity - 2 mods = 4 open"


def test_high_ilvl_white_base_qualifies_on_base_score_alone():
    item = {"typeLine": "Ancestral Tiara", "baseType": "Ancestral Tiara",
            "frameType": 0, "ilvl": 82}
    score, reason = score_gear(item, MODS, BASES)
    assert score >= 4
    assert "ilvl" in reason or "base" in reason


def test_runeforged_base_scores_above_its_plain_twin():
    plain = {"typeLine": "Bronze Greaves", "baseType": "Bronze Greaves",
             "frameType": 0, "ilvl": 81}
    runed = {"typeLine": "Runeforged Bronze Greaves",
             "baseType": "Runeforged Bronze Greaves", "frameType": 0, "ilvl": 81}
    assert score_gear(runed, MODS, BASES)[0] > score_gear(plain, MODS, BASES)[0]


def test_avoid_base_is_penalised():
    good = {"typeLine": "Sinister Quarterstaff", "baseType": "Sinister Quarterstaff",
            "frameType": 0, "ilvl": 82}
    bad = {"typeLine": "Dreaming Quarterstaff", "baseType": "Dreaming Quarterstaff",
           "frameType": 0, "ilvl": 82}
    assert score_gear(bad, MODS, BASES)[0] < score_gear(good, MODS, BASES)[0]


def test_corrupted_unique_always_escalates():
    item = {"name": "Mageblood", "frameType": 3, "corrupted": True}
    entry = IndexEntry("Mageblood", 100.0, 10, {})
    assert should_search_unique(item, entry, UNIQUES) == "corrupted"


def test_chase_priced_unique_escalates():
    item = {"name": "Mageblood", "frameType": 3}
    entry = IndexEntry("Mageblood", 135416.55, 5808, {})
    assert should_search_unique(item, entry, UNIQUES) == "chase-price"


def test_swingy_unique_with_a_good_roll_escalates():
    """The Ventor's case: index reports the floor, our copy is not the floor."""
    metadata = {"explicit_mods": ["+(5-15)% to Fire Resistance"]}
    item = {"name": "Ventor's Gamble", "frameType": 3,
            "explicitMods": ["+15% to Fire Resistance"]}
    entry = IndexEntry("Ventor's Gamble", 7.0, 26747, metadata)
    assert should_search_unique(item, entry, UNIQUES) == "swingy-good-roll"


def test_swingy_unique_with_a_bad_roll_does_not_escalate():
    metadata = {"explicit_mods": ["+(1-111)% increased Evasion and Energy Shield"]}
    item = {"name": "Thunderfist", "frameType": 3,
            "explicitMods": ["+2% increased Evasion and Energy Shield"]}
    entry = IndexEntry("Thunderfist", 3.0, 10179, metadata)
    assert should_search_unique(item, entry, UNIQUES) is None


def test_cheap_unique_does_not_escalate_even_with_a_perfect_roll():
    """A perfect copy of a 3ex item is still a 3ex item.

    Thunderfist really does spread x111, so spread AND roll are both
    satisfied by a well-rolled copy. Only the price floor stops it eating a
    search slot.
    """
    metadata = {"explicit_mods": ["+(1-111)% increased Evasion and Energy Shield"]}
    item = {"name": "Thunderfist", "frameType": 3,
            "explicitMods": ["+111% increased Evasion and Energy Shield"]}
    entry = IndexEntry("Thunderfist", 3.0, 10179, metadata)
    assert should_search_unique(item, entry, UNIQUES) is None


def test_budgets_cap_each_class_independently():
    items = [(rare(["+96 to maximum Life", "+35% increased Movement Speed",
                    "+40% increased Critical Hit Chance"]), 0) for _ in range(50)]
    chosen = select(items, {}, Budgets(rares=3, bases=2, uniques=1, endgame=1),
                    MODS, BASES, UNIQUES)
    assert len([c for c in chosen if c.item_class.name == "GEAR"]) <= 3


def test_endgame_items_are_always_candidates_since_no_index_exists():
    items = [({"typeLine": "Waystone (Tier 16)", "frameType": 2, "ilvl": 82}, 0)]
    chosen = select(items, {}, Budgets(), MODS, BASES, UNIQUES)
    assert [c.item_class.name for c in chosen] == ["ENDGAME"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_candidates.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sox.valuation.candidates'`

- [ ] **Step 3: Implement `src/sox/valuation/candidates.py`**

```python
"""Decide which items are worth spending a search on.

Budget is counted in searches, not items, and split per class so cheap base
searches cannot starve the rare searches.
"""

from __future__ import annotations

from dataclasses import dataclass

from sox.config import Budgets
from sox.scout import IndexEntry
from sox.valuation.allowlists import BaseRules, ModEntry, UniqueRules
from sox.valuation.classify import ItemClass, Rarity, classify, display_name, rarity_of
from sox.valuation.mods import coherence_bonus, match_mod, score_mods
from sox.valuation.rolls import roll_score, spread_of

AVOID_PENALTY = 3

# Affix capacity by rarity: rare is 3 prefixes + 3 suffixes.
AFFIX_CAPACITY = {Rarity.RARE: 6, Rarity.MAGIC: 2, Rarity.NORMAL: 0}

MAX_OPEN_BONUS_PREMIUM = 3
MAX_OPEN_BONUS_ORDINARY = 1


@dataclass(frozen=True)
class Candidate:
    item: dict
    tab: int
    item_class: ItemClass
    score: int
    reason: str


def _base_name(item: dict) -> str:
    return item.get("baseType") or item.get("typeLine") or ""


def used_affixes(item: dict) -> int:
    """Distinct mods occupying a prefix or suffix slot.

    Fractured and crafted mods occupy slots like any other. They are unioned
    rather than summed because the endpoint sometimes repeats a fractured mod
    in explicitMods, and double-counting would understate the open space.
    """
    mods: set[str] = set()
    for key in ("explicitMods", "fracturedMods", "craftedMods", "desecratedMods"):
        mods.update(item.get(key) or [])
    return len(mods)


def open_affix_bonus(item: dict, mod_score: int, has_premium: bool) -> tuple[int, str]:
    """Room left to craft is part of what a buyer pays for.

    Corrupted and mirrored items score nothing here: neither can be modified
    again, so their empty slots are permanently empty. Treating one as a craft
    base is not a tuning preference, it is simply wrong.
    """
    if item.get("corrupted") or item.get("mirrored"):
        return 0, ""

    rarity = rarity_of(item)
    # Normal items are excluded: their value IS open affix space, and the base
    # score (ilvl + base type + rune family) already measures it.
    if rarity not in (Rarity.RARE, Rarity.MAGIC):
        return 0, ""

    open_slots = AFFIX_CAPACITY[rarity] - used_affixes(item)
    if open_slots <= 0:
        return 0, ""

    if has_premium:
        bonus = min(open_slots, MAX_OPEN_BONUS_PREMIUM)
    elif mod_score >= 4:
        bonus = min(open_slots, MAX_OPEN_BONUS_ORDINARY)
    else:
        # A blank rare also has open slots and is not worth a search.
        return 0, ""
    return bonus, f"open{open_slots}"


def score_gear(item: dict, mods: list[ModEntry], base_rules: BaseRules) -> tuple[int, str]:
    """Score an item on its mods and on its value as a crafting base."""
    ilvl = int(item.get("ilvl") or 0)
    base = _base_name(item)
    reasons = []

    item_mods = list(item.get("explicitMods") or []) + list(item.get("fracturedMods") or [])
    mod_score, by_category = score_mods(item_mods, mods)
    if mod_score:
        reasons.append(f"mods={mod_score}")
    # Many mods serving one archetype means a real buyer exists for the set.
    bonus, why = coherence_bonus(item_mods, mods)
    if bonus:
        mod_score += bonus
        reasons.append(why)

    # A locked-in high-tier mod with room left to craft is what sells.
    has_premium = any(
        (entry := match_mod(text, mods)) is not None and entry.weight >= 3
        for text in item_mods
    )
    bonus, open_reason = open_affix_bonus(item, mod_score, has_premium)
    if bonus:
        mod_score += bonus
        reasons.append(open_reason)

    base_score = 0
    for min_ilvl, weight in base_rules.ilvl_tiers:
        if ilvl >= min_ilvl:
            base_score += weight
            reasons.append(f"ilvl{min_ilvl}+")
            break

    named = base_rules.named.get(base)
    if named:
        base_score += named
        reasons.append("named-base")

    for prefix, bonus in base_rules.rune_prefixes.items():
        if base.startswith(prefix + " "):
            base_score += bonus
            reasons.append(prefix.lower())
            break

    if base in base_rules.avoid:
        base_score -= AVOID_PENALTY
        reasons.append("avoid-base")

    rarity = rarity_of(item)
    if rarity is Rarity.RARE:
        # Rares qualify on mods; a strong base is a tiebreak, not the point.
        total = mod_score + (1 if base_score >= 4 else 0)
    else:
        total = base_score + mod_score

    return total, ",".join(reasons) or "none"


def qualifies(item: dict, score: int) -> bool:
    ilvl = int(item.get("ilvl") or 0)
    rarity = rarity_of(item)
    if rarity is Rarity.RARE:
        return score >= 6 or (score >= 4 and ilvl >= 80)
    return score >= 4


def should_search_unique(
    item: dict,
    entry: IndexEntry | None,
    rules: UniqueRules,
) -> str | None:
    """Return the escalation reason, or None to take the index price."""
    if item.get("corrupted"):
        return "corrupted"
    if entry is None:
        return None
    if entry.price_ex >= rules.thresholds.get("chase_price_ex", 5000):
        return "chase-price"

    if spread_of(entry.metadata) < rules.thresholds.get("swing_ratio", 2.0):
        return None

    # A perfect copy of a worthless item is still worthless. Thunderfist
    # spreads x111 at ~3ex and would otherwise satisfy both clauses above.
    if entry.price_ex < rules.thresholds.get("min_escalation_price_ex", 50):
        return None

    score = roll_score(item.get("explicitMods") or [], entry.metadata)
    if score is None:
        return None
    if score >= rules.thresholds.get("roll_score_percentile", 0.75):
        return "swingy-good-roll"
    return None


def select(
    items: list[tuple[dict, int]],
    index: dict[str, IndexEntry],
    budgets: Budgets,
    mods: list[ModEntry],
    base_rules: BaseRules,
    unique_rules: UniqueRules,
) -> list[Candidate]:
    buckets: dict[str, list[Candidate]] = {"rares": [], "bases": [], "uniques": [], "endgame": []}

    for item, tab in items:
        item_class = classify(item)

        if item_class is ItemClass.ENDGAME:
            # No index covers these at all, so every one is a candidate.
            buckets["endgame"].append(Candidate(item, tab, item_class, 0, "no-index"))
            continue

        if item_class is ItemClass.UNIQUE:
            entry = index.get(display_name(item))
            reason = should_search_unique(item, entry, unique_rules)
            if reason:
                weight = unique_rules.entries.get(display_name(item), 0)
                buckets["uniques"].append(Candidate(item, tab, item_class, weight, reason))
            continue

        if item_class is ItemClass.GEAR:
            score, reason = score_gear(item, mods, base_rules)
            if not qualifies(item, score):
                continue
            key = "rares" if rarity_of(item) is Rarity.RARE else "bases"
            buckets[key].append(Candidate(item, tab, item_class, score, reason))

    caps = {"rares": budgets.rares, "bases": budgets.bases,
            "uniques": budgets.uniques, "endgame": budgets.endgame}

    chosen: list[Candidate] = []
    for key, candidates in buckets.items():
        candidates.sort(key=lambda c: -c.score)
        chosen.extend(candidates[: caps[key]])
    return chosen
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_candidates.py -v`
Expected: 11 passed

- [ ] **Step 5: Commit**

```bash
git add src/sox/valuation/candidates.py tests/test_candidates.py
git commit -m "feat(candidates): score items and spend the search budget per class"
```

---

## Task 15: Query builder and relaxation ladder

**Files:**
- Create: `src/sox/valuation/query.py`
- Test: `tests/test_query.py`

**Interfaces:**
- Consumes: `ModEntry` (Task 13), `match_mod` (Task 13), `Candidate` (Task 14).
- Produces: `build_query(item: dict, category: str, mods: list[ModEntry], status: str = "online", relax: int = 0) -> dict`, `category_for(item: dict, base_rules) -> str | None`, `RELAX_STEPS: tuple[float, ...]`, `query_hash(query: dict) -> str`.

The query semantics are the load-bearing part: **minimums only, never maximums**, and search by item *type*, never by item name. That returns items at least as good as ours, so the cheapest result is a ceiling on our ask.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_query.py
from sox.valuation.allowlists import load_mods
from sox.valuation.query import RELAX_STEPS, build_query, query_hash

MODS = load_mods()

CHEST = {
    "typeLine": "Vaal Cuirass", "baseType": "Vaal Cuirass", "frameType": 2, "ilvl": 82,
    "explicitMods": ["+96 to maximum Life", "+40% increased Critical Hit Chance"],
    "properties": [{"name": "Energy Shield", "values": [["412", 0]]},
                   {"name": "Armour", "values": [["180", 0]]}],
}


def test_searches_by_category_never_by_name():
    query = build_query(CHEST, "armour.chest", MODS)
    filters = query["query"]["filters"]["type_filters"]["filters"]
    assert filters["category"]["option"] == "armour.chest"
    assert "term" not in query["query"]
    assert "name" not in query["query"]


def test_uses_nonunique_rarity_and_ilvl_minimum():
    filters = build_query(CHEST, "armour.chest", MODS)["query"]["filters"]["type_filters"]["filters"]
    assert filters["rarity"]["option"] == "nonunique"
    assert filters["ilvl"] == {"min": 82}


def test_defences_are_minimums_with_no_maximum():
    equipment = build_query(CHEST, "armour.chest", MODS)["query"]["filters"]["equipment_filters"]["filters"]
    assert equipment["es"] == {"min": 412}
    assert equipment["ar"] == {"min": 180}
    assert all("max" not in v for v in equipment.values())


def test_stats_are_minimums_at_our_values():
    stats = build_query(CHEST, "armour.chest", MODS)["query"]["stats"][0]
    assert stats["type"] == "and"
    life = [f for f in stats["filters"] if "3299347043" in str(f["id"])][0]
    assert life["value"] == {"min": 96}
    assert "max" not in life["value"]


def test_ambiguous_mod_becomes_an_or_group():
    item = {"typeLine": "Vaal Cuirass", "baseType": "Vaal Cuirass", "frameType": 2,
            "ilvl": 82, "explicitMods": ["+30 to Spirit"]}
    groups = build_query(item, "armour.chest", MODS)["query"]["stats"]
    or_groups = [g for g in groups if g["type"] == "count"]
    assert or_groups, "an ambiguous mod must search across all its ids"
    assert len(or_groups[0]["filters"]) == 2
    assert or_groups[0]["value"] == {"min": 1}


def test_unmatched_mods_are_omitted_not_guessed():
    item = {**CHEST, "explicitMods": ["Grants Interpretive Dance"]}
    assert build_query(item, "armour.chest", MODS)["query"]["stats"][0]["filters"] == []


def test_relaxation_scales_minimums_down():
    strict = build_query(CHEST, "armour.chest", MODS, relax=0)
    relaxed = build_query(CHEST, "armour.chest", MODS, relax=1)
    strict_es = strict["query"]["filters"]["equipment_filters"]["filters"]["es"]["min"]
    relaxed_es = relaxed["query"]["filters"]["equipment_filters"]["filters"]["es"]["min"]
    assert relaxed_es < strict_es
    assert relaxed_es == int(412 * RELAX_STEPS[1])


def test_query_hash_is_stable_and_order_independent():
    assert query_hash({"a": 1, "b": 2}) == query_hash({"b": 2, "a": 1})
    assert query_hash({"a": 1}) != query_hash({"a": 2})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_query.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sox.valuation.query'`

- [ ] **Step 3: Implement `src/sox/valuation/query.py`**

```python
"""Build a trade2 query.

The search is NOT "find my item". It is "find the cheapest item at least as
good as mine", so every constraint is a minimum at our item's value and there
are no maximums. Every listing that comes back is therefore >= ours on every
constrained axis, which makes the cheapest one a CEILING on our ask rather
than a comparable sale.
"""

from __future__ import annotations

import hashlib
import json

from sox.valuation.allowlists import ModEntry
from sox.valuation.mods import match_mod
from sox.valuation.rolls import parse_values

# Relaxation ladder: full strictness, then progressively looser minimums.
RELAX_STEPS = (1.0, 0.9, 0.75)

# Property name in the stash JSON -> equipment_filters id, verified against
# /api/trade2/data/filters.
DEFENCE_PROPERTIES = {
    "Energy Shield": "es",
    "Armour": "ar",
    "Evasion Rating": "ev",
    "Runic Ward": "ward",
    "Spirit": "spirit",
}


def _property(item: dict, name: str) -> int | None:
    for prop in item.get("properties") or []:
        if prop.get("name") == name:
            values = prop.get("values") or []
            if values and values[0]:
                try:
                    return int(str(values[0][0]).split()[0])
                except (ValueError, IndexError):
                    return None
    return None


def build_query(
    item: dict,
    category: str,
    mods: list[ModEntry],
    status: str = "online",
    relax: int = 0,
) -> dict:
    scale = RELAX_STEPS[min(relax, len(RELAX_STEPS) - 1)]

    type_filters: dict = {
        "category": {"option": category},
        "rarity": {"option": "nonunique"},
    }
    ilvl = int(item.get("ilvl") or 0)
    if ilvl:
        type_filters["ilvl"] = {"min": int(ilvl * scale)}

    equipment: dict = {}
    for prop_name, filter_id in DEFENCE_PROPERTIES.items():
        value = _property(item, prop_name)
        if value:
            equipment[filter_id] = {"min": int(value * scale)}

    and_filters: list[dict] = []
    or_groups: list[dict] = []
    for text in item.get("explicitMods") or []:
        entry = match_mod(text, mods)
        if entry is None:
            continue                      # never guess a stat id
        values = parse_values(text)
        if not values:
            continue
        minimum = int(values[0] * scale)

        if entry.ambiguous:
            # Several stat ids share this mod text; match any of them.
            or_groups.append({
                "type": "count",
                "value": {"min": 1},
                "filters": [
                    {"id": stat_id, "value": {"min": minimum}} for stat_id in entry.ids
                ],
            })
        else:
            and_filters.append({"id": entry.ids[0], "value": {"min": minimum}})

    query: dict = {
        "query": {
            "status": {"option": status},
            "filters": {"type_filters": {"filters": type_filters}},
            "stats": [{"type": "and", "filters": and_filters}, *or_groups],
        },
        "sort": {"price": "asc"},
    }
    if equipment:
        query["query"]["filters"]["equipment_filters"] = {"filters": equipment}
    return query


def query_hash(query: dict) -> str:
    return hashlib.sha256(json.dumps(query, sort_keys=True).encode()).hexdigest()[:16]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_query.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add src/sox/valuation/query.py tests/test_query.py
git commit -m "feat(query): open-ended minimum queries with an OR group for ambiguous mods"
```

---

## Task 16: Trade client

**Files:**
- Create: `src/sox/ggg/trade.py`
- Test: `tests/test_trade.py`

**Interfaces:**
- Consumes: `GGGSession` (Task 5), `Cache` (Task 3).
- Produces: `Listing(price_ex: float, currency: str, account: str)`, `TradeClient(session, cache, league: str)` with `.search(query: dict) -> tuple[str, list[str]]`, `.fetch(query_id: str, hashes: list[str]) -> list[Listing]`, `.stats() -> dict`, `.filters() -> dict`; `FETCH_BATCH = 10`.

The fetch endpoint accepts at most 10 hashes per call — that limit is why budget is counted in calls, not items.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trade.py
import httpx

from sox.cache import Cache
from sox.ggg.governor import RateGovernor
from sox.ggg.session import GGGSession
from sox.ggg.trade import FETCH_BATCH, TradeClient


def build(handler, tmp_path):
    gov = RateGovernor(clock=lambda: 0.0, sleeper=lambda s: None)
    session = GGGSession("s", gov, httpx.Client(transport=httpx.MockTransport(handler)), "t")
    return TradeClient(session, Cache(tmp_path / "c.sqlite"), league="Runes of Aldur")


def test_search_posts_to_the_poe2_endpoint_and_returns_hashes(tmp_path):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["method"] = request.method
        return httpx.Response(200, json={"id": "abc", "result": ["h1", "h2"]})

    query_id, hashes = build(handler, tmp_path).search({"query": {}})
    assert seen["method"] == "POST"
    assert "/api/trade2/search/poe2/Runes%20of%20Aldur" in seen["url"] or \
           "/api/trade2/search/poe2/Runes of Aldur" in seen["url"]
    assert query_id == "abc"
    assert hashes == ["h1", "h2"]


def test_fetch_batches_at_ten_hashes(tmp_path):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={"result": [
            {"listing": {"price": {"amount": 5, "currency": "exalted"},
                         "account": {"name": "someone"}}},
        ]})

    client = build(handler, tmp_path)
    client.fetch("abc", [f"h{i}" for i in range(23)])
    assert len(calls) == 3, "23 hashes must split into 10/10/3"
    assert FETCH_BATCH == 10


def test_fetch_parses_listings(tmp_path):
    def handler(request):
        return httpx.Response(200, json={"result": [
            {"listing": {"price": {"amount": 12.5, "currency": "exalted"},
                         "account": {"name": "seller"}}},
            {"listing": {"price": None, "account": {"name": "nobuyout"}}},
        ]})

    listings = build(handler, tmp_path).fetch("abc", ["h1"])
    assert len(listings) == 1, "listings without a buyout price are dropped"
    assert listings[0].price_ex == 12.5
    assert listings[0].account == "seller"


def test_stats_are_cached_between_clients(tmp_path):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={"result": [{"id": "explicit", "entries": []}]})

    cache = Cache(tmp_path / "c.sqlite")
    transport = httpx.MockTransport(handler)
    gov = RateGovernor(clock=lambda: 0.0, sleeper=lambda s: None)

    for _ in range(2):
        session = GGGSession("s", gov, httpx.Client(transport=transport), "t")
        TradeClient(session, cache, "L").stats()
    assert calls["n"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_trade.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sox.ggg.trade'`

- [ ] **Step 3: Implement `src/sox/ggg/trade.py`**

```python
"""trade2 search/fetch. Unofficial, Cloudflare-fronted, rate limited."""

from __future__ import annotations

from dataclasses import dataclass

from sox.cache import TTL, Cache
from sox.ggg.session import GGGSession

BASE = "https://www.pathofexile.com/api/trade2"
FETCH_BATCH = 10       # the fetch endpoint accepts at most 10 hashes per call


@dataclass(frozen=True)
class Listing:
    price_ex: float
    currency: str
    account: str


class TradeClient:
    def __init__(self, session: GGGSession, cache: Cache, league: str) -> None:
        self._session = session
        self._cache = cache
        self._league = league

    def search(self, query: dict) -> tuple[str, list[str]]:
        payload = self._session.post(
            f"{BASE}/search/poe2/{self._league}", json=query
        ).json()
        return payload.get("id", ""), payload.get("result", [])

    def fetch(self, query_id: str, hashes: list[str]) -> list[Listing]:
        listings: list[Listing] = []
        for start in range(0, len(hashes), FETCH_BATCH):
            batch = hashes[start : start + FETCH_BATCH]
            payload = self._session.get(
                f"{BASE}/fetch/{','.join(batch)}", params={"query": query_id}
            ).json()
            for result in payload.get("result") or []:
                listing = (result or {}).get("listing") or {}
                price = listing.get("price")
                if not price or price.get("amount") is None:
                    continue        # no buyout: not a usable data point
                listings.append(Listing(
                    price_ex=float(price["amount"]),
                    currency=price.get("currency", ""),
                    account=(listing.get("account") or {}).get("name", ""),
                ))
        return listings

    def stats(self) -> dict:
        return self._cached("stats_data", "/data/stats")

    def filters(self) -> dict:
        return self._cached("filters_data", "/data/filters")

    def _cached(self, table: str, path: str) -> dict:
        cached = self._cache.get(table, path)
        if cached is not None:
            return cached
        payload = self._session.get(BASE + path).json()
        self._cache.put(table, path, payload, ttl=TTL[table])
        return payload
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_trade.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/sox/ggg/trade.py tests/test_trade.py
git commit -m "feat(trade): trade2 search/fetch client with cached metadata"
```

---

## Task 17: Trade pricing with the relaxation ladder

**Files:**
- Create: `src/sox/valuation/trade_pricer.py`
- Test: `tests/test_trade_pricer.py`

**Interfaces:**
- Consumes: `TradeClient` (Task 16), `build_query`/`query_hash`/`RELAX_STEPS` (Task 15), `Candidate` (Task 14), `Cache` (Task 3).
- Produces: `TradeResult(ceiling_ex: float | None, suggested_ask_ex: float | None, tag: str, listings: int, searches_used: int)`, `price_by_search(candidate, category, mods, trade, cache, status, min_results=5) -> TradeResult`; `SUGGESTED_ASK_FACTOR = 0.9`.

Tags: `exact`, `relaxed:N`, `unpriced:above-market`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trade_pricer.py
from sox.cache import Cache
from sox.valuation.allowlists import load_mods
from sox.valuation.candidates import Candidate
from sox.valuation.classify import ItemClass
from sox.valuation.trade_pricer import SUGGESTED_ASK_FACTOR, price_by_search

MODS = load_mods()

ITEM = {"typeLine": "Vaal Cuirass", "baseType": "Vaal Cuirass", "frameType": 2,
        "ilvl": 82, "explicitMods": ["+96 to maximum Life"]}
CANDIDATE = Candidate(ITEM, 0, ItemClass.GEAR, 7, "mods=7")


class FakeTrade:
    """Returns a scripted number of listings per successive search."""

    def __init__(self, per_call):
        self.per_call = list(per_call)
        self.searches = 0

    def search(self, query):
        self.searches += 1
        count = self.per_call[min(self.searches - 1, len(self.per_call) - 1)]
        return f"q{self.searches}", [f"h{i}" for i in range(count)]

    def fetch(self, query_id, hashes):
        from sox.ggg.trade import Listing
        return [Listing(price_ex=10.0 * (i + 1), currency="exalted", account="a")
                for i in range(len(hashes))]


def test_enough_results_at_full_strictness_is_exact(tmp_path):
    trade = FakeTrade([6])
    result = price_by_search(CANDIDATE, "armour.chest", MODS, trade,
                             Cache(tmp_path / "c.sqlite"), "online")
    assert result.tag == "exact"
    assert trade.searches == 1


def test_cheapest_listing_is_the_ceiling(tmp_path):
    """Every listing is >= ours, so the cheapest is a ceiling on our ask."""
    trade = FakeTrade([6])
    result = price_by_search(CANDIDATE, "armour.chest", MODS, trade,
                             Cache(tmp_path / "c.sqlite"), "online")
    assert result.ceiling_ex == 10.0
    assert result.suggested_ask_ex == 10.0 * SUGGESTED_ASK_FACTOR


def test_too_few_results_relaxes_and_tags_the_step(tmp_path):
    trade = FakeTrade([0, 0, 7])
    result = price_by_search(CANDIDATE, "armour.chest", MODS, trade,
                             Cache(tmp_path / "c.sqlite"), "online")
    assert result.tag == "relaxed:2"
    assert trade.searches == 3


def test_nothing_found_after_the_ladder_is_flagged_not_zeroed(tmp_path):
    """No results means nothing that good is listed — the best items land here."""
    trade = FakeTrade([0, 0, 0])
    result = price_by_search(CANDIDATE, "armour.chest", MODS, trade,
                             Cache(tmp_path / "c.sqlite"), "online")
    assert result.tag == "unpriced:above-market"
    assert result.ceiling_ex is None


def test_ladder_is_bounded(tmp_path):
    trade = FakeTrade([0])
    result = price_by_search(CANDIDATE, "armour.chest", MODS, trade,
                             Cache(tmp_path / "c.sqlite"), "online")
    assert trade.searches <= 3
    assert result.searches_used == trade.searches


def test_repeat_run_is_served_from_cache(tmp_path):
    cache = Cache(tmp_path / "c.sqlite")
    first = FakeTrade([6])
    price_by_search(CANDIDATE, "armour.chest", MODS, first, cache, "online")
    second = FakeTrade([6])
    result = price_by_search(CANDIDATE, "armour.chest", MODS, second, cache, "online")
    assert second.searches == 0, "a cached price must cost no budget"
    assert result.ceiling_ex == 10.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_trade_pricer.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sox.valuation.trade_pricer'`

- [ ] **Step 3: Implement `src/sox/valuation/trade_pricer.py`**

```python
"""Search, fetch, and turn listings into a price.

Zero results is information, not an error: it means nothing at least as good
as our item is currently listed. Those items are the ones most worth looking
at by hand, so they are flagged rather than dropped.
"""

from __future__ import annotations

from dataclasses import dataclass

from sox.cache import TTL, Cache
from sox.valuation.allowlists import ModEntry
from sox.valuation.candidates import Candidate
from sox.valuation.query import RELAX_STEPS, build_query, query_hash

SUGGESTED_ASK_FACTOR = 0.9
FETCH_LIMIT = 10          # one fetch call is enough to price the cheap end


@dataclass(frozen=True)
class TradeResult:
    ceiling_ex: float | None
    suggested_ask_ex: float | None
    tag: str
    listings: int
    searches_used: int


def price_by_search(
    candidate: Candidate,
    category: str,
    mods: list[ModEntry],
    trade,
    cache: Cache,
    status: str,
    min_results: int = 5,
) -> TradeResult:
    searches = 0

    for step in range(len(RELAX_STEPS)):
        query = build_query(candidate.item, category, mods, status=status, relax=step)
        key = query_hash(query)

        cached = cache.get("trade_price", key)
        if cached is not None:
            return TradeResult(**cached)

        query_id, hashes = trade.search(query)
        searches += 1
        if len(hashes) < min_results:
            continue

        listings = trade.fetch(query_id, hashes[:FETCH_LIMIT])
        if not listings:
            continue

        cheapest = min(listing.price_ex for listing in listings)
        result = TradeResult(
            ceiling_ex=cheapest,
            suggested_ask_ex=round(cheapest * SUGGESTED_ASK_FACTOR, 2),
            tag="exact" if step == 0 else f"relaxed:{step}",
            listings=len(listings),
            searches_used=searches,
        )
        cache.put("trade_price", key, result.__dict__, ttl=TTL["trade_price"])
        return result

    return TradeResult(None, None, "unpriced:above-market", 0, searches)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_trade_pricer.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/sox/valuation/trade_pricer.py tests/test_trade_pricer.py
git commit -m "feat(trade-pricer): relaxation ladder producing a ceiling, not a comp"
```

---

## Task 18: Wire trade search into the pipeline

**Files:**
- Modify: `src/sox/pipeline.py`
- Modify: `src/sox/report.py`
- Modify: `src/sox/cli.py`
- Test: `tests/test_pipeline_trade.py`

**Interfaces:**
- Consumes: everything from Tasks 12–17.
- Produces: `value_stash(..., trade=None, mods=None, base_rules=None, unique_rules=None) -> tuple[list[PricedItem], League]` — same signature plus optional trade dependencies. When `trade is None`, behaviour is exactly Milestone 1.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline_trade.py
import httpx

from sox.cache import Cache
from sox.config import Budgets, Config
from sox.ggg.governor import RateGovernor
from sox.ggg.session import GGGSession
from sox.ggg.trade import Listing
from sox.pipeline import value_stash
from sox.scout import ScoutClient
from sox.valuation.allowlists import load_bases, load_mods, load_uniques

LEAGUES = [{"Value": "L", "ShortName": "l", "IsCurrent": True,
            "DivinePrice": 336.5, "BaseCurrencyText": "Exalted Orb"}]

RARE = {"typeLine": "Vaal Cuirass", "baseType": "Vaal Cuirass", "frameType": 2,
        "ilvl": 82,
        "explicitMods": ["+96 to maximum Life", "+35% increased Movement Speed",
                         "+40% increased Critical Hit Chance"]}


def ggg_handler(request):
    if request.url.params.get("tabs") == "1":
        return httpx.Response(200, json={"tabs": [
            {"n": "Gear", "i": 0, "id": "a", "type": "NormalStash"}]})
    return httpx.Response(200, json={"items": [RARE]})


def scout_handler(request):
    if request.url.path.endswith("/Leagues"):
        return httpx.Response(200, json=LEAGUES)
    return httpx.Response(200, json={"Items": []})


class FakeTrade:
    def __init__(self):
        self.searches = 0

    def search(self, query):
        self.searches += 1
        return "q", [f"h{i}" for i in range(8)]

    def fetch(self, query_id, hashes):
        return [Listing(price_ex=250.0, currency="exalted", account="a")]


def build(tmp_path):
    gov = RateGovernor(clock=lambda: 0.0, sleeper=lambda s: None)
    session = GGGSession("s", gov, httpx.Client(transport=httpx.MockTransport(ggg_handler)), "t")
    scout = ScoutClient(httpx.Client(transport=httpx.MockTransport(scout_handler)),
                        Cache(tmp_path / "c.sqlite"), "t")
    return session, scout


def test_no_trade_client_means_no_searches(tmp_path):
    session, scout = build(tmp_path)
    items, _ = value_stash(session, scout, Config(), "me", "L", None)
    assert items[0].total_ex is None
    assert items[0].tag == "unpriced:no-index"


def test_qualifying_rare_is_priced_by_search(tmp_path):
    session, scout = build(tmp_path)
    trade = FakeTrade()
    items, _ = value_stash(
        session, scout, Config(budgets=Budgets(rares=5)), "me", "L", None,
        trade=trade, cache=Cache(tmp_path / "t.sqlite"),
        mods=load_mods(), base_rules=load_bases(), unique_rules=load_uniques(),
    )
    priced = items[0]
    assert trade.searches == 1
    assert priced.total_ex == 250.0
    assert priced.source == "trade"
    assert priced.tag == "exact"


def test_budget_zero_disables_searching(tmp_path):
    session, scout = build(tmp_path)
    trade = FakeTrade()
    value_stash(
        session, scout, Config(budgets=Budgets(rares=0, bases=0, uniques=0, endgame=0)),
        "me", "L", None, trade=trade, cache=Cache(tmp_path / "t.sqlite"),
        mods=load_mods(), base_rules=load_bases(), unique_rules=load_uniques(),
    )
    assert trade.searches == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_pipeline_trade.py -v`
Expected: FAIL — `TypeError: value_stash() got an unexpected keyword argument 'trade'`

- [ ] **Step 3: Rewrite `src/sox/pipeline.py`**

```python
"""Wires the pieces into one valuation run."""

from __future__ import annotations

from dataclasses import replace

from sox.cache import Cache
from sox.config import Config
from sox.ggg import stash
from sox.ggg.session import GGGSession
from sox.scout import League, ScoutClient
from sox.valuation.allowlists import BaseRules, ModEntry, UniqueRules
from sox.valuation.candidates import select
from sox.valuation.classify import ItemClass
from sox.valuation.index_pricer import PricedItem, price_from_index
from sox.valuation.trade_pricer import price_by_search

# Item class -> the trade category to search it under. Verified against
# type_filters.category.
ENDGAME_CATEGORIES = {
    "Waystone": "map.waystone",
    "Tablet": "map.tablet",
    "Relic": "sanctum.relic",
    "Charm": "flask.charm",
}


def _category_for(item: dict, base_rules: BaseRules) -> str | None:
    name = item.get("baseType") or item.get("typeLine") or ""
    for marker, category in ENDGAME_CATEGORIES.items():
        if marker in name:
            return category
    # Gear: fall back to the first tracked slot whose suffix matches the base.
    # A precise mapping needs the item's inventory slot, which the legacy
    # endpoint supplies as `inventoryId` on equipped items only.
    return item.get("category") or None


def value_stash(
    session: GGGSession,
    scout: ScoutClient,
    cfg: Config,
    account: str,
    league: str,
    tabs: list[int] | None,
    trade=None,
    cache: Cache | None = None,
    mods: list[ModEntry] | None = None,
    base_rules: BaseRules | None = None,
    unique_rules: UniqueRules | None = None,
) -> tuple[list[PricedItem], League]:
    league_info = scout.current_league()
    index = scout.prices(league_info.short)

    available = stash.list_tabs(session, account, league)
    selected = [t for t in available if tabs is None or t.index in tabs]

    raw: list[tuple[dict, int]] = []
    for tab in selected:
        for item in stash.fetch_tab(session, account, league, tab.index):
            raw.append((item, tab.index))

    priced = [price_from_index(item, tab, index) for item, tab in raw]

    if trade is None or mods is None or base_rules is None or unique_rules is None:
        return priced, league_info

    candidates = select(raw, index, cfg.budgets, mods, base_rules, unique_rules)
    by_identity = {id(c.item): c for c in candidates}

    upgraded: list[PricedItem] = []
    for (item, tab), item_priced in zip(raw, priced):
        candidate = by_identity.get(id(item))
        if candidate is None:
            upgraded.append(item_priced)
            continue

        category = _category_for(item, base_rules)
        if category is None:
            upgraded.append(item_priced)
            continue

        result = price_by_search(
            candidate, category, mods, trade, cache or Cache(cfg.cache_path), cfg.status
        )
        if result.ceiling_ex is None:
            upgraded.append(replace(item_priced, tag=result.tag))
            continue

        upgraded.append(replace(
            item_priced,
            unit_price_ex=result.ceiling_ex,
            total_ex=result.ceiling_ex * item_priced.stack,
            source="trade",
            tag=result.tag,
        ))
    return upgraded, league_info
```

- [ ] **Step 4: Make the gear category resolvable**

The rare in the test has no `category` key, so `_category_for` returns `None` and no search happens. Map the base name to a slot using the base allowlist. Replace `_category_for` with:

```python
# Base-name suffix -> trade category. Covers the slots in base_allowlist.toml.
BASE_SUFFIX_CATEGORIES = {
    "Cuirass": "armour.chest", "Vest": "armour.chest", "Robe": "armour.chest",
    "Ringmail": "armour.chest", "Armour": "armour.chest", "Mantle": "armour.chest",
    "Tiara": "armour.helmet", "Crown": "armour.helmet", "Cap": "armour.helmet",
    "Helm": "armour.helmet", "Greaves": "armour.boots", "Sandals": "armour.boots",
    "Boots": "armour.boots", "Gloves": "armour.gloves", "Mitts": "armour.gloves",
    "Bracers": "armour.gloves", "Wraps": "armour.gloves", "Cuffs": "armour.gloves",
    "Shield": "armour.shield", "Buckler": "armour.buckler", "Focus": "armour.focus",
    "Quiver": "armour.quiver", "Amulet": "accessory.amulet", "Ring": "accessory.ring",
    "Belt": "accessory.belt", "Sash": "accessory.belt",
    "Quarterstaff": "weapon.warstaff", "Spear": "weapon.spear", "Bow": "weapon.bow",
    "Crossbow": "weapon.crossbow", "Wand": "weapon.wand", "Sceptre": "weapon.sceptre",
    "Staff": "weapon.staff", "Maul": "weapon.twomace",
}


def _category_for(item: dict, base_rules: BaseRules) -> str | None:
    name = item.get("baseType") or item.get("typeLine") or ""
    for marker, category in ENDGAME_CATEGORIES.items():
        if marker in name:
            return category
    for suffix, category in BASE_SUFFIX_CATEGORIES.items():
        if name.endswith(suffix):
            return category
    return None
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_pipeline_trade.py -v`
Expected: 3 passed

- [ ] **Step 6: Show price source and tag in the report**

In `src/sox/report.py`, change the top-items line in `render` to include the tag:

```python
    for item in ranked:
        stack = f" x{item.stack}" if item.stack > 1 else ""
        tag = f"  [{item.tag}]" if item.tag else ""
        lines.append(f"  {_fmt(item.total_ex, divine_ratio):>28}  {item.name}{stack}{tag}")
```

Add a test to `tests/test_report.py`:

```python
def test_render_marks_ceiling_prices_as_such():
    items = [PricedItem("Vaal Cuirass", ItemClass.GEAR, 250.0, 1, 250.0,
                        "trade", "exact", 0)]
    text = render(items, summarize(items), divine_ratio=336.5)
    assert "exact" in text
```

- [ ] **Step 7: Wire the trade client into the CLI**

In `src/sox/cli.py`, replace the `value_stash(...)` call with:

```python
        trade = None
        mods = base_rules = unique_rules = None
        if not args.no_trade:
            from sox.ggg.trade import TradeClient
            from sox.valuation.allowlists import load_bases, load_mods, load_uniques

            trade = TradeClient(session, cache, league)
            mods, base_rules, unique_rules = load_mods(), load_bases(), load_uniques()

        items, league_info = value_stash(
            session, scout, cfg, cfg.account, league, tabs,
            trade=trade, cache=cache, mods=mods,
            base_rules=base_rules, unique_rules=unique_rules,
        )
```

- [ ] **Step 8: Run the whole suite**

Run: `uv run pytest -q`
Expected: all green

- [ ] **Step 9: Commit**

```bash
git add src/sox/pipeline.py src/sox/report.py src/sox/cli.py tests/test_pipeline_trade.py tests/test_report.py
git commit -m "feat(pipeline): price qualifying items by trade search within budget"
```

---

## Task 19: `sox diff`

**Files:**
- Modify: `src/sox/report.py`
- Modify: `src/sox/cli.py`
- Test: `tests/test_diff.py`

**Interfaces:**
- Consumes: snapshots written by `write_snapshot` (Task 10).
- Produces: `Change(name: str, before_ex: float | None, after_ex: float | None, delta_ex: float)`, `diff_snapshots(before: dict, after: dict) -> tuple[list[Change], float]`, `render_diff(changes, total_delta, divine_ratio) -> str`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_diff.py
from sox.report import diff_snapshots, render_diff


def snapshot(items, total):
    return {"league": "L", "divine_ratio_ex": 336.5,
            "totals": {"total_ex": total, "by_tab": {}, "unpriced": 0},
            "items": items}


BEFORE = snapshot([
    {"name": "Exalted Orb", "total_ex": 400.0},
    {"name": "Mageblood", "total_ex": 130000.0},
], 130400.0)

AFTER = snapshot([
    {"name": "Exalted Orb", "total_ex": 500.0},
    {"name": "Headhunter", "total_ex": 29279.25},
], 29779.25)


def test_reports_total_delta():
    _, delta = diff_snapshots(BEFORE, AFTER)
    assert delta == 29779.25 - 130400.0


def test_detects_gains_losses_and_new_items():
    changes, _ = diff_snapshots(BEFORE, AFTER)
    by_name = {c.name: c for c in changes}
    assert by_name["Exalted Orb"].delta_ex == 100.0
    assert by_name["Mageblood"].after_ex is None      # gone
    assert by_name["Headhunter"].before_ex is None    # new
    assert by_name["Headhunter"].delta_ex == 29279.25


def test_unchanged_items_are_omitted():
    changes, _ = diff_snapshots(BEFORE, BEFORE)
    assert changes == []


def test_render_orders_by_absolute_impact():
    changes, delta = diff_snapshots(BEFORE, AFTER)
    text = render_diff(changes, delta, 336.5)
    assert text.index("Mageblood") < text.index("Exalted Orb")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_diff.py -v`
Expected: FAIL — `ImportError: cannot import name 'diff_snapshots'`

- [ ] **Step 3: Append to `src/sox/report.py`**

```python
@dataclass(frozen=True)
class Change:
    name: str
    before_ex: float | None
    after_ex: float | None
    delta_ex: float


def _totals_by_name(snapshot: dict) -> dict[str, float]:
    out: dict[str, float] = {}
    for item in snapshot.get("items", []):
        value = item.get("total_ex")
        if value is None:
            continue
        out[item["name"]] = out.get(item["name"], 0.0) + value
    return out


def diff_snapshots(before: dict, after: dict) -> tuple[list[Change], float]:
    old, new = _totals_by_name(before), _totals_by_name(after)
    changes = []
    for name in sorted(set(old) | set(new)):
        was, now = old.get(name), new.get(name)
        delta = (now or 0.0) - (was or 0.0)
        if delta == 0:
            continue
        changes.append(Change(name, was, now, round(delta, 2)))

    total_delta = round(
        after["totals"]["total_ex"] - before["totals"]["total_ex"], 2
    )
    return changes, total_delta


def render_diff(changes: list[Change], total_delta: float, divine_ratio: float) -> str:
    sign = "+" if total_delta >= 0 else ""
    lines = [f"Total change: {sign}{_fmt(total_delta, divine_ratio)}", ""]
    for change in sorted(changes, key=lambda c: -abs(c.delta_ex)):
        sign = "+" if change.delta_ex >= 0 else ""
        state = ""
        if change.before_ex is None:
            state = " (new)"
        elif change.after_ex is None:
            state = " (gone)"
        lines.append(f"  {sign}{change.delta_ex:>14,.2f} ex  {change.name}{state}")
    return "\n".join(lines)
```

- [ ] **Step 4: Add the `diff` subcommand in `src/sox/cli.py`**

In `build_parser`, after the `value` parser:

```python
    diff = sub.add_parser("diff", help="compare two snapshots")
    diff.add_argument("before", type=Path)
    diff.add_argument("after", type=Path)
```

In `main`, before the account check (diff needs neither an account nor a token):

```python
    if args.command == "diff":
        before = json.loads(args.before.read_text())
        after = json.loads(args.after.read_text())
        changes, total_delta = report.diff_snapshots(before, after)
        print(report.render_diff(changes, total_delta,
                                 after.get("divine_ratio_ex", 0.0)))
        return 0
```

Add `import json` at the top of `cli.py`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_diff.py -v`
Expected: 4 passed

- [ ] **Step 6: Run the whole suite**

Run: `uv run pytest -q`
Expected: all green (≈95 tests)

- [ ] **Step 7: Commit**

```bash
git add src/sox/report.py src/sox/cli.py tests/test_diff.py
git commit -m "feat(diff): compare two snapshots and rank changes by impact"
```

---

## Self-review notes

Checked against the spec:

- **Covered:** legacy stash reads (T6), rate governor (T4), trade2 search/fetch (T16), scout index (T7), gems as their own class (T8/T9), endgame classes with no index (T8/T18), unique escalation incl. the Ventor's case (T14), open-ended minimum queries (T15), relaxation ladder (T17), candidate scoring and per-class budgets (T14), cache TTLs (T3), POESESSID handling and redaction (T2/T5), stack-size multiplication (T9), report/snapshot/diff (T10/T19), no-network test suite (throughout).
- **Deliberately deferred, and why:** `corrupted`/`mirrored`/`fractured` are read for unique escalation (T14) but are not yet added as `misc_filters` constraints on gear searches — that is a refinement once real search results exist to compare against. Mirrored items should be excluded from gear searches before this is considered finished; add it when Task 18's category mapping is validated against live data.
- **Known provisional:** Task 6's parser is the only component not verified against its live service. Its Step 6 is the verification gate.
- **Scale sanity:** a cold run costs ~24 scout calls (cached 6h), 1 tab list, N tab fetches, and at most `20+15+10+10 = 55` searches plus their fetches.
