"""SQLite persistence for backtest runs (separate from paper trades.db)."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trader.config import REPO_ROOT

DB_PATH = REPO_ROOT / "data" / "backtests" / "runs.db"


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    with _connect() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
              id TEXT PRIMARY KEY,
              created_at TEXT NOT NULL,
              start_date TEXT NOT NULL,
              end_date TEXT NOT NULL,
              cash REAL NOT NULL,
              cost_bps REAL NOT NULL,
              fill_rule TEXT NOT NULL,
              status TEXT NOT NULL,
              error TEXT,
              metrics_json TEXT,
              params_json TEXT
            );
            CREATE TABLE IF NOT EXISTS equity_points (
              run_id TEXT NOT NULL,
              dt TEXT NOT NULL,
              strategy_equity REAL NOT NULL,
              spy_equity REAL NOT NULL,
              drawdown REAL NOT NULL,
              PRIMARY KEY (run_id, dt)
            );
            CREATE TABLE IF NOT EXISTS simulated_trades (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              run_id TEXT NOT NULL,
              dt TEXT NOT NULL,
              sleeve TEXT,
              symbol TEXT NOT NULL,
              side TEXT NOT NULL,
              qty REAL NOT NULL,
              price REAL NOT NULL,
              notional REAL NOT NULL,
              cost REAL NOT NULL,
              end_value REAL,
              meta_json TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_trades_run ON simulated_trades(run_id);
            """
        )
        # migrate equity_points cash breakdown columns
        cols = {r[1] for r in c.execute("PRAGMA table_info(equity_points)").fetchall()}
        for col in ("cash", "cash_free", "cash_skim", "cash_div"):
            if col not in cols:
                c.execute(f"ALTER TABLE equity_points ADD COLUMN {col} REAL")


def _eq_num(p: dict[str, Any], *keys: str) -> float | None:
    for k in keys:
        if p.get(k) is not None:
            try:
                return float(p[k])
            except (TypeError, ValueError):
                return None
    return None


def save_run(result: dict[str, Any]) -> str:
    init_db()
    run_id = result.get("id") or uuid.uuid4().hex[:12]
    with _connect() as c:
        c.execute(
            """INSERT OR REPLACE INTO runs
               (id, created_at, start_date, end_date, cash, cost_bps, fill_rule,
                status, error, metrics_json, params_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                result.get("created_at") or datetime.now(timezone.utc).isoformat(),
                result["start_date"],
                result["end_date"],
                float(result["cash"]),
                float(result["cost_bps"]),
                result["fill_rule"],
                result.get("status") or "ok",
                result.get("error"),
                json.dumps(result.get("metrics") or {}),
                json.dumps(result.get("params") or {}),
            ),
        )
        c.execute("DELETE FROM equity_points WHERE run_id=?", (run_id,))
        c.execute("DELETE FROM simulated_trades WHERE run_id=?", (run_id,))
        eq_rows = []
        for p in result.get("equity") or []:
            cash = _eq_num(p, "cash")
            free = _eq_num(p, "cash_free")
            skim = _eq_num(p, "cash_skim")
            div = _eq_num(p, "cash_div")
            # Reconstruct total cash if only buckets present
            if cash is None and any(v is not None for v in (free, skim, div)):
                cash = float(free or 0) + float(skim or 0) + float(div or 0)
            eq_rows.append(
                (
                    run_id,
                    p["dt"],
                    float(p["strategy_equity"]),
                    float(p["spy_equity"]),
                    float(p["drawdown"]),
                    cash,
                    free,
                    skim,
                    div,
                )
            )
        c.executemany(
            """INSERT INTO equity_points
               (run_id, dt, strategy_equity, spy_equity, drawdown,
                cash, cash_free, cash_skim, cash_div)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            eq_rows,
        )
        tr_rows = [
            (
                run_id,
                t["dt"],
                t.get("sleeve"),
                t["symbol"],
                t.get("side") or "buy",
                float(t["qty"]),
                float(t["price"]),
                float(t["notional"]),
                float(t.get("cost") or 0),
                t.get("end_value"),
                json.dumps(t.get("meta") or {}),
            )
            for t in result.get("trades") or []
        ]
        c.executemany(
            """INSERT INTO simulated_trades
               (run_id, dt, sleeve, symbol, side, qty, price, notional, cost, end_value, meta_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            tr_rows,
        )
    return run_id


def list_runs(limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    with _connect() as c:
        rows = c.execute(
            """SELECT id, created_at, start_date, end_date, cash, cost_bps, fill_rule,
                      status, metrics_json, params_json FROM runs
               ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    out = []
    for r in rows:
        m = json.loads(r["metrics_json"] or "{}")
        p = json.loads(r["params_json"] or "{}")
        full = m.get("full") or {}
        out.append(
            {
                "id": r["id"],
                "created_at": r["created_at"],
                "start_date": r["start_date"],
                "end_date": r["end_date"],
                "cash": r["cash"],
                "cost_bps": r["cost_bps"],
                "fill_rule": r["fill_rule"],
                "red_lookback": int(p.get("red_lookback") or 1),
                "mode": p.get("mode") or p.get("strategy") or "buy_hold",
                "year_end_skim": bool(p.get("year_end_skim")),
                "skim_pct": p.get("skim_pct"),
                "universe": p.get("universe") or "balanced",
                "status": r["status"],
                "total_return": full.get("total_return"),
                "vs_spy": full.get("vs_spy"),
                "max_drawdown": full.get("max_drawdown"),
                "trade_count": m.get("trade_count"),
                "dividend_cash": (m.get("dividends") or {}).get("total_cash"),
            }
        )
    return out


def load_run(run_id: str) -> dict[str, Any] | None:
    init_db()
    with _connect() as c:
        r = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if not r:
            return None
        eq = c.execute(
            """SELECT dt, strategy_equity, spy_equity, drawdown,
                      cash, cash_free, cash_skim, cash_div
               FROM equity_points WHERE run_id=? ORDER BY dt""",
            (run_id,),
        ).fetchall()
        tr = c.execute(
            """SELECT dt, sleeve, symbol, side, qty, price, notional, cost, end_value, meta_json
               FROM simulated_trades WHERE run_id=? ORDER BY dt, id""",
            (run_id,),
        ).fetchall()
    params = json.loads(r["params_json"] or "{}")

    def _row_cash(row: sqlite3.Row) -> dict[str, Any]:
        keys = row.keys()
        cash = row["cash"] if "cash" in keys else None
        free = row["cash_free"] if "cash_free" in keys else None
        skim = row["cash_skim"] if "cash_skim" in keys else None
        div = row["cash_div"] if "cash_div" in keys else None
        if cash is None and any(v is not None for v in (free, skim, div)):
            cash = float(free or 0) + float(skim or 0) + float(div or 0)
        invested = (
            None
            if cash is None
            else float(row["strategy_equity"]) - float(cash)
        )
        return {
            "dt": row["dt"],
            "strategy_equity": row["strategy_equity"],
            "spy_equity": row["spy_equity"],
            "drawdown": row["drawdown"],
            "cash": cash,
            "cash_free": free,
            "cash_skim": skim,
            "cash_div": div,
            "invested": invested,
        }

    return {
        "id": r["id"],
        "created_at": r["created_at"],
        "start_date": r["start_date"],
        "end_date": r["end_date"],
        "cash": r["cash"],
        "cost_bps": r["cost_bps"],
        "fill_rule": r["fill_rule"],
        "red_lookback": int(params.get("red_lookback") or 1),
        "mode": params.get("mode") or params.get("strategy") or "buy_hold",
        "year_end_skim": bool(params.get("year_end_skim")) or (params.get("mode") == "year_end_skim"),
        "skim_pct": params.get("skim_pct"),
        "universe": params.get("universe") or "balanced",
        "status": r["status"],
        "error": r["error"],
        "metrics": json.loads(r["metrics_json"] or "{}"),
        "params": params,
        "equity": [_row_cash(row) for row in eq],
        "trades": [
            {
                "dt": row["dt"],
                "sleeve": row["sleeve"],
                "symbol": row["symbol"],
                "side": row["side"],
                "qty": row["qty"],
                "price": row["price"],
                "notional": row["notional"],
                "cost": row["cost"],
                "end_value": row["end_value"],
                "meta": json.loads(row["meta_json"] or "{}"),
            }
            for row in tr
        ],
    }

