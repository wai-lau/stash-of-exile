"""On-disk cache. A cache hit costs no rate-limit budget, which is the point."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

TTL = {
    "filters_data": 7 * 86400,
    "stats_data": 7 * 86400,
    "index_price": 6 * 3600,
    "trade_price": 12 * 3600,
    "exchange_static": 7 * 86400,
    "exchange_book": 6 * 3600,
    "exchange_fills": 3600,  # the snapshot behind it is hourly
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
    """Never store a secret here. Prices and API metadata only.

    The cache is an optimisation, never a dependency: if it cannot be read or
    written the tool still prices items, it just pays the API calls again. A
    long-running watch session must not die because the file was replaced or
    removed underneath it.
    """

    def __init__(self, path: Path, clock: Callable[[], float] = time.time) -> None:
        self._path = path
        self._clock = clock
        self._conn: sqlite3.Connection | None = None
        # The exchange snapshot is fetched on a thread and read on the main
        # one. sqlite ties a connection to the thread that opened it, and a
        # crossing raises a sqlite3.Error like any other — which the
        # reconnect below used to swallow, handing the connection to
        # whichever thread asked last and losing the row to the other. One
        # connection, shared, and a lock so no two statements interleave.
        self._lock = threading.Lock()
        self._connect()

    def _connect(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self._path, check_same_thread=False)
            self._conn.execute(_SCHEMA)
            self._conn.commit()
        except (sqlite3.Error, OSError):
            self._conn = None

    def get(self, table: str, key: str) -> Any | None:
        row = self._row(table, key)
        if row is None:
            return None
        value, expires_at = row
        if self._clock() > expires_at:
            return None
        return json.loads(value)

    def peek(self, table: str, key: str) -> Any | None:
        """The entry whether or not it has expired.

        For the one caller that would rather start on last hour's answer
        than wait for this hour's: a watch session prices its first item on
        the snapshot the cache holds while the fresh one is fetched behind it.
        """
        row = self._row(table, key)
        return None if row is None else json.loads(row[0])

    def _row(self, table: str, key: str) -> tuple[str, float] | None:
        with self._lock:
            if self._conn is None:
                return None
            try:
                return self._conn.execute(
                    "SELECT value, expires_at FROM entries WHERE tbl = ? AND key = ?",
                    (table, key),
                ).fetchone()
            except sqlite3.Error:
                self._connect()
                return None

    def put(self, table: str, key: str, value: Any, ttl: int) -> None:
        with self._lock:
            self._put(table, key, value, ttl)

    def _put(self, table: str, key: str, value: Any, ttl: int) -> None:
        for attempt in (1, 2):
            if self._conn is None:
                self._connect()
                if self._conn is None:
                    return
            try:
                self._conn.execute(
                    "INSERT OR REPLACE INTO entries (tbl, key, value, expires_at) "
                    "VALUES (?, ?, ?, ?)",
                    (table, key, json.dumps(value), self._clock() + ttl),
                )
                self._conn.commit()
                return
            except sqlite3.Error:
                # The file may have been replaced or removed underneath us.
                # Reconnect once, then give up quietly.
                self._conn = None
                if attempt == 2:
                    return

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
