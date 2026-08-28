"""SQLite + JSONL journal under TRADING_LOG_DIR."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trader.db import connect as _db_connect

log = logging.getLogger("trader")

try:
    _JOURNAL_MAX_BYTES = int(os.getenv("JOURNAL_MAX_BYTES", str(100 * 1024 * 1024)))
except ValueError:
    _JOURNAL_MAX_BYTES = 100 * 1024 * 1024
try:
    _JOURNAL_KEEP_ROTATED = int(os.getenv("JOURNAL_KEEP_ROTATED", "10"))
except ValueError:
    _JOURNAL_KEEP_ROTATED = 10


def _maybe_rotate_journal(path: Path) -> None:
    """Rotate journal.jsonl when it exceeds JOURNAL_MAX_BYTES.

    Strategy: rename current file to ``journal.jsonl.YYYYMMDD-HHMMSS`` (no
    compression — kept plain so weekly_report.load_jsonl_since can glob them).
    Prunes oldest rotated files beyond JOURNAL_KEEP_ROTATED.

    Set JOURNAL_MAX_BYTES=0 to disable rotation entirely.
    """
    if _JOURNAL_MAX_BYTES <= 0:
        return
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size < _JOURNAL_MAX_BYTES:
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    rotated = path.with_name(f"{path.name}.{stamp}")
    try:
        path.rename(rotated)
        log.info("journal_rotated path=%s size=%d to=%s", path, size, rotated.name)
    except OSError as e:
        log.warning("journal_rotate_failed path=%s err=%s", path, e)
        return
    try:
        rotated_files = sorted(
            path.parent.glob(f"{path.name}.*"),
            key=lambda p: p.stat().st_mtime,
        )
        excess = len(rotated_files) - _JOURNAL_KEEP_ROTATED
        for old in rotated_files[: max(0, excess)]:
            try:
                old.unlink()
                log.info("journal_rotated_pruned path=%s", old)
            except OSError:
                pass
    except OSError:
        pass

# execute_status values:
#   placed       — order_id returned, Alpaca accepted
#   skipped      — executor chose not to send (qty<1, no position, action=HOLD, etc.)
#   error        — Alpaca or network error; order was NOT placed
#   not_attempted — action was HOLD/HALT; executor never called
_STATUS_PLACED = "placed"
_STATUS_SKIPPED = "skipped"
_STATUS_ERROR = "error"
_STATUS_NOT_ATTEMPTED = "not_attempted"


def _ensure_columns(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()}
    migrations = [
        ("logic_json", "TEXT"),
        ("execute_status", "TEXT"),
        ("order_id", "TEXT"),
        ("execute_error", "TEXT"),
    ]
    for col, typ in migrations:
        if col not in cols:
            conn.execute(f"ALTER TABLE trades ADD COLUMN {col} {typ}")


def init_db() -> None:
    from trader import config

    conn = _db_connect(config.DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT, symbol TEXT, action TEXT, strategy TEXT,
        confidence REAL, size_pct REAL, stop_loss REAL, take_profit REAL,
        rationale TEXT, equity REAL, daily_pnl REAL,
        logic_json TEXT,
        execute_status TEXT,
        order_id TEXT,
        execute_error TEXT
    )"""
    )
    _ensure_columns(conn)
    conn.commit()
    conn.close()


def snapshot_excerpt(snapshot: dict) -> dict[str, Any]:
    ac = snapshot.get("account") or {}
    return {
        "symbol": snapshot.get("symbol"),
        "price": snapshot.get("price"),
        "timestamp": snapshot.get("timestamp"),
        "position": snapshot.get("position"),
        "open_orders": snapshot.get("open_orders"),
        "indicators": snapshot.get("indicators"),
        "options": snapshot.get("options"),
        "macro": snapshot.get("macro"),
        "vix": snapshot.get("vix"),
        "fear_greed": snapshot.get("fear_greed"),
        "analyst_recommendation": snapshot.get("analyst_recommendation"),
        "data_quality": snapshot.get("data_quality"),
        "market_regime": snapshot.get("market_regime"),
        "symbol_sector": snapshot.get("symbol_sector"),
        "portfolio_sector_notional_pct": (
            (snapshot.get("portfolio_context") or {}).get("sector_notional_pct")
        ),
        "news_sentiment": snapshot.get("news_sentiment"),
        "news_headlines": snapshot.get("news_headlines"),
        "news_feed_errors": snapshot.get("news_feed_errors"),
        "earnings_days_away": snapshot.get("earnings_days_away"),
        "next_earnings_date": snapshot.get("next_earnings_date"),
        "account": {
            "equity": ac.get("equity"),
            "cash": ac.get("cash"),
            "daily_pnl": ac.get("daily_pnl"),
            "open_positions": ac.get("open_positions"),
            "daytrade_count": ac.get("daytrade_count"),
            "pattern_day_trader": ac.get("pattern_day_trader"),
        },
    }


def build_logic_json(
    snapshot: dict,
    raw_decision: dict,
    final_decision: dict,
    execute_result: dict[str, Any] | None,
    debate: dict[str, Any] | None = None,
    automated_exit: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from trader.config import TRADING_PROFILE

    out: dict[str, Any] = {
        "snapshot_excerpt": snapshot_excerpt(snapshot),
        "raw_decision": raw_decision,
        "final_decision": final_decision,
        "execute": execute_result,
        "trading_profile": TRADING_PROFILE,
    }
    if debate is not None:
        out["debate"] = debate
    if automated_exit is not None:
        out["automated_exit"] = automated_exit
    return out


def _derive_execute_fields(
    execute_result: dict[str, Any] | None,
    action: str | None,
) -> tuple[str, str | None, str | None]:
    """Return (execute_status, order_id, execute_error)."""
    if action not in ("BUY", "SELL") or execute_result is None:
        return _STATUS_NOT_ATTEMPTED, None, None

    if not isinstance(execute_result, dict):
        return _STATUS_ERROR, None, repr(execute_result)

    status = execute_result.get("status", "")
    order_id = execute_result.get("order_id") or None

    if order_id:
        return _STATUS_PLACED, order_id, None

    raw_reason = execute_result.get("reason") or ""
    if status == "skipped":
        return _STATUS_SKIPPED, None, raw_reason or None
    if status == "error":
        return _STATUS_ERROR, None, raw_reason or None

    # Fallback: anything with a status string but no order_id
    if status:
        return status, None, raw_reason or None

    return _STATUS_ERROR, None, raw_reason or "unknown"


def write_journal_entry(
    symbol: str,
    snapshot: dict,
    decision: dict,
    logic: dict[str, Any],
) -> None:
    from trader import config

    init_db()
    logic_str = json.dumps(logic, default=str)
    action = decision.get("action")
    execute_result = logic.get("execute")

    execute_status, order_id, execute_error = _derive_execute_fields(execute_result, action)

    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "action": action,
        "strategy": decision.get("strategy"),
        "confidence": decision.get("confidence"),
        "size_pct": decision.get("size_pct"),
        "stop_loss": decision.get("stop_loss"),
        "take_profit": decision.get("take_profit"),
        "rationale": decision.get("rationale"),
        "equity": snapshot["account"]["equity"],
        "daily_pnl": snapshot["account"]["daily_pnl"],
        "logic_json": logic_str,
        "execute_status": execute_status,
        "order_id": order_id,
        "execute_error": execute_error,
    }
    conn = _db_connect(config.DB_PATH)
    conn.execute(
        """INSERT INTO trades
        (timestamp,symbol,action,strategy,confidence,size_pct,stop_loss,take_profit,
         rationale,equity,daily_pnl,logic_json,execute_status,order_id,execute_error)
        VALUES
        (:timestamp,:symbol,:action,:strategy,:confidence,:size_pct,:stop_loss,:take_profit,
         :rationale,:equity,:daily_pnl,:logic_json,:execute_status,:order_id,:execute_error)""",
        row,
    )
    conn.commit()
    conn.close()

    jsonl_row = {k: v for k, v in row.items() if k != "logic_json"}
    jsonl_row["logic"] = logic
    jsonl_path = Path(config.JSONL_PATH)
    _maybe_rotate_journal(jsonl_path)
    with open(jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(jsonl_row, default=str) + "\n")
