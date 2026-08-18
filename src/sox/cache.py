"""On-disk cache. A cache hit costs no rate-limit budget, which is the point."""

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
