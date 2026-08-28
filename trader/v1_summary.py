"""Build a frozen v1 experiment summary from a trades SQLite DB."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _counts(conn: sqlite3.Connection, sql: str) -> list[dict[str, Any]]:
    rows = conn.execute(sql).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append({"key": r[0] if r[0] is not None else "", "count": int(r[1])})
    return out


def build_v1_summary(db_path: Path) -> dict[str, Any]:
    """Aggregate journal stats for the retired v1 experiment."""
    conn = sqlite3.connect(db_path)
    try:
        total = int(conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0])
        if total == 0:
            return {
                "version": 1,
                "title": "v1 trading experiment",
                "retired": "2026-07-30",
                "total_cycles": 0,
                "empty": True,
            }

        first = conn.execute(
            "SELECT timestamp, equity FROM trades ORDER BY id ASC LIMIT 1"
        ).fetchone()
        last = conn.execute(
            "SELECT timestamp, equity FROM trades ORDER BY id DESC LIMIT 1"
        ).fetchone()
        eq_mm = conn.execute(
            "SELECT MIN(CAST(equity AS REAL)), MAX(CAST(equity AS REAL)) "
            "FROM trades WHERE equity IS NOT NULL"
        ).fetchone()
        symbols = int(conn.execute("SELECT COUNT(DISTINCT symbol) FROM trades").fetchone()[0])
        placed = int(
            conn.execute(
                "SELECT COUNT(*) FROM trades WHERE execute_status='placed'"
            ).fetchone()[0]
        )
        errors = int(
            conn.execute(
                "SELECT COUNT(*) FROM trades WHERE execute_status='error'"
            ).fetchone()[0]
        )

        start_eq = float(first[1]) if first and first[1] is not None else 100000.0
        end_eq = float(last[1]) if last and last[1] is not None else start_eq
        peak_eq = float(eq_mm[1]) if eq_mm and eq_mm[1] is not None else end_eq
        floor_eq = float(eq_mm[0]) if eq_mm and eq_mm[0] is not None else end_eq
        pnl = end_eq - start_eq
        pnl_pct = (pnl / start_eq * 100.0) if start_eq else 0.0
        max_dd = peak_eq - floor_eq
        max_dd_pct = (max_dd / peak_eq * 100.0) if peak_eq else 0.0

        profile_counts: Counter[str] = Counter()
        for (lj,) in conn.execute(
            "SELECT logic_json FROM trades WHERE logic_json IS NOT NULL"
        ):
            try:
                j = json.loads(lj)
                profile_counts[str(j.get("trading_profile") or "unknown")] += 1
            except Exception:
                profile_counts["unparseable"] += 1

        top_symbols = [
            {"symbol": r[0], "count": int(r[1])}
            for r in conn.execute(
                """
                SELECT symbol, COUNT(*) c FROM trades
                WHERE execute_status='placed'
                GROUP BY symbol ORDER BY c DESC LIMIT 15
                """
            )
        ]

        buy_strategies = _counts(
            conn,
            """
            SELECT strategy, COUNT(*) c FROM trades
            WHERE upper(action)='BUY' GROUP BY strategy ORDER BY c DESC
            """,
        )

        return {
            "version": 1,
            "title": "v1 trading experiment",
            "retired": "2026-07-30",
            "why": (
                "Too complex and high-maintenance: regime → LLM brain → risk → "
                "options/stocks execution → weekly Sonnet review."
            ),
            "empty": False,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "period": {
                "start": first[0] if first else None,
                "end": last[0] if last else None,
            },
            "total_cycles": total,
            "unique_symbols": symbols,
            "actions": _counts(
                conn,
                "SELECT action, COUNT(*) c FROM trades GROUP BY action ORDER BY c DESC",
            ),
            "execute_status": _counts(
                conn,
                "SELECT COALESCE(execute_status,''), COUNT(*) c FROM trades "
                "GROUP BY 1 ORDER BY c DESC",
            ),
            "buy_strategies": buy_strategies,
            "profiles": [
                {"key": k, "count": v} for k, v in profile_counts.most_common()
            ],
            "orders_placed": placed,
            "orders_errored": errors,
            "equity": {
                "start": round(start_eq, 2),
                "end": round(end_eq, 2),
                "peak": round(peak_eq, 2),
                "floor": round(floor_eq, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "max_drawdown": round(max_dd, 2),
                "max_drawdown_pct": round(max_dd_pct, 2),
            },
            "top_symbols_placed": top_symbols,
            "artifacts": {
                "db": "data/archive/v1/trades.db",
                "jsonl": "data/archive/v1/journal.jsonl",
                "code": "archive/v1/",
                "tarball": "/srv/archive/ai-trading-bot-v1-20260730.tar.gz",
            },
        }
    finally:
        conn.close()


def write_summary(summary: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
