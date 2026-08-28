"""SQLite connection helper — WAL mode + busy_timeout for safe multi-process access.

The trading bot writes ``trades.db`` from one systemd unit while the read-only
FastAPI viewer reads it from another. The default journal mode (``delete``) only
allows one connection at a time and surfaces ``database is locked`` errors when
the viewer happens to read mid-write. WAL mode lets readers stay open while a
writer commits — the right model for our long-running paper experiment.

We also raise ``busy_timeout`` from SQLite's default 5 s to 15 s so any
short-lived contention (e.g. a slow EOD aggregation while the brain finishes a
cycle) waits instead of raising.

All trader modules MUST go through ``connect()``; do not call
``sqlite3.connect()`` directly except inside one-off scripts.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from trader.config import DB_PATH


def _busy_timeout_ms() -> int:
    raw = (os.getenv("TRADING_DB_BUSY_TIMEOUT_MS") or "").strip()
    try:
        v = int(raw) if raw else 15000
    except ValueError:
        v = 15000
    return max(1000, min(60000, v))


def _wal_enabled() -> bool:
    raw = (os.getenv("TRADING_DB_WAL") or "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    # busy_timeout first so the WAL pragma itself can wait if another process holds the lock
    conn.execute(f"PRAGMA busy_timeout = {_busy_timeout_ms()}")
    if _wal_enabled():
        try:
            conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.OperationalError:
            # Locked by another writer transitioning the journal — the existing mode
            # (likely WAL after first run) is fine; carry on without raising.
            pass
        # NORMAL is safe under WAL and dramatically reduces fsync cost vs FULL.
        conn.execute("PRAGMA synchronous = NORMAL")


def connect(path: Path | str | None = None, *, row_factory: Any = None) -> sqlite3.Connection:
    """Return a sqlite3 connection with WAL + busy_timeout pragmas applied.

    ``row_factory`` defaults to None (tuple rows). Pass ``sqlite3.Row`` for dict-style
    access. The caller still owns the connection and is responsible for closing it
    (or using ``with connect() as conn:`` for a transactional block).
    """
    p = Path(path) if path is not None else DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    if row_factory is not None:
        conn.row_factory = row_factory
    _apply_pragmas(conn)
    return conn
