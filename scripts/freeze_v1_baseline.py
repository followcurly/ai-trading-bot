#!/usr/bin/env python3
"""Freeze live journal into data/archive/v1 and reset live DB/JSONL for v2 baseline."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from trader.config import DB_PATH, JSONL_PATH, REPO_ROOT  # noqa: E402
from trader.journal import init_db  # noqa: E402
from trader.v1_summary import build_v1_summary, write_summary  # noqa: E402

ARCHIVE = REPO_ROOT / "data" / "archive" / "v1"


def main() -> int:
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    if not DB_PATH.is_file():
        print(f"no live DB at {DB_PATH}", file=sys.stderr)
        return 1

    dest_db = ARCHIVE / "trades.db"
    dest_jsonl = ARCHIVE / "journal.jsonl"
    summary_path = ARCHIVE / "summary.json"

    # Checkpoint WAL so the copy is consistent, then copy.
    import sqlite3

    with sqlite3.connect(DB_PATH) as conn:
        try:
            conn.execute("PRAGMA wal_checkpoint(FULL)")
        except sqlite3.Error:
            pass

    shutil.copy2(DB_PATH, dest_db)
    if JSONL_PATH.is_file():
        shutil.copy2(JSONL_PATH, dest_jsonl)
    else:
        dest_jsonl.write_text("", encoding="utf-8")

    summary = build_v1_summary(dest_db)
    write_summary(summary, summary_path)
    print(
        f"frozen v1: cycles={summary.get('total_cycles')} "
        f"end_equity={summary.get('equity', {}).get('end')} → {ARCHIVE}"
    )

    # Reset live journal for v2 baseline
    for p in (DB_PATH, Path(str(DB_PATH) + "-wal"), Path(str(DB_PATH) + "-shm")):
        if p.is_file():
            p.unlink()
    if JSONL_PATH.is_file():
        rotated = JSONL_PATH.with_suffix(
            JSONL_PATH.suffix + ".v1-final"
        )
        # Keep one backup next to live path if not already moved
        if not rotated.is_file():
            shutil.move(str(JSONL_PATH), str(rotated))
        elif JSONL_PATH.is_file():
            JSONL_PATH.unlink()
    init_db()
    JSONL_PATH.parent.mkdir(parents=True, exist_ok=True)
    JSONL_PATH.touch(exist_ok=True)
    print(f"live journal reset: {DB_PATH} + {JSONL_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
