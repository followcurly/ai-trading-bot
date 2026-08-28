"""Build research evaluation packs from journal SQLite + JSONL."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from trader.config import DB_PATH, JSONL_PATH, TRADING_PROFILE
from trader.reporting.chart_svg import load_equity_series

PROMOTION_THRESHOLDS = {
    "min_closed_trades": 30,
    "min_win_rate": 0.45,
    "min_profit_factor": 1.1,
    "max_drawdown_pct": 0.12,
}


def _cutoff_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _parse_logic(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        d = json.loads(raw)
        return d if isinstance(d, dict) else {}
    except json.JSONDecodeError:
        return {}


def _profile_matches(logic: dict[str, Any], profile: str | None) -> bool:
    if not profile:
        return True
    p = (logic.get("trading_profile") or "").strip().lower()
    if p:
        return p == profile.strip().lower()
    return profile.strip().lower() == "default"


def _exit_reason(logic: dict[str, Any], action: str | None) -> str:
    ae = logic.get("automated_exit")
    if isinstance(ae, dict) and ae.get("kind"):
        return str(ae["kind"])
    fd = logic.get("final_decision") if isinstance(logic.get("final_decision"), dict) else {}
    kind = fd.get("risk_exit_kind")
    if kind:
        return str(kind)
    rat = (logic.get("final_decision") or {}).get("rationale") if isinstance(
        logic.get("final_decision"), dict
    ) else ""
    if not rat:
        rat = str(logic.get("rationale") or "")
    for tag in (
        "bracket_take_profit",
        "bracket_stop",
        "profit_ladder",
        "trail_giveback",
        "expiry_sweep",
        "options_hard_stop",
        "emergency_daily_loss",
    ):
        if tag in rat:
            return tag
    if (action or "").upper() == "SELL":
        return "discretionary_sell"
    return "other"


def drawdown_series(equity_rows: list[tuple[str, float]]) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    peak = 0.0
    for ts, eq in equity_rows:
        peak = max(peak, eq)
        dd = ((eq - peak) / peak * 100.0) if peak > 0 else 0.0
        out.append((ts, dd))
    return out


def build_eval_pack(
    *,
    db_path: Path | None = None,
    days: int = 30,
    profile: str | None = None,
) -> dict[str, Any]:
    db = db_path or DB_PATH
    prof = profile if profile is not None else TRADING_PROFILE
    cutoff = _cutoff_iso(days)
    equity_rows = load_equity_series(db, cutoff)
    dd_rows = drawdown_series(equity_rows)

    by_action: Counter[str] = Counter()
    by_execute: Counter[str] = Counter()
    closed: list[dict[str, Any]] = []
    pnl_by_exit: dict[str, float] = {}
    exit_reason_counts: Counter[str] = Counter()

    if db.is_file():
        try:
            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT timestamp, symbol, action, strategy, equity, daily_pnl,
                          logic_json, execute_status
                   FROM trades WHERE timestamp > ? ORDER BY id ASC""",
                (cutoff,),
            ).fetchall()
            conn.close()
        except sqlite3.Error:
            rows = []
    else:
        rows = []

    for r in rows:
        logic = _parse_logic(r["logic_json"])
        if not _profile_matches(logic, prof):
            continue
        act = (r["action"] or "").upper()
        by_action[act] += 1
        ex_st = (r["execute_status"] or "").strip() or "unknown"
        by_execute[ex_st] += 1
        if act != "SELL":
            continue
        reason = _exit_reason(logic, act)
        exit_reason_counts[reason] += 1
        ae = logic.get("automated_exit") if isinstance(logic.get("automated_exit"), dict) else {}
        detail = ae.get("detail") if isinstance(ae.get("detail"), dict) else {}
        pnl = detail.get("realized_pnl")
        if pnl is None:
            pnl = detail.get("unrealized_pnl_at_exit")
        try:
            pnl_f = float(pnl) if pnl is not None else None
        except (TypeError, ValueError):
            pnl_f = None
        if pnl_f is not None:
            pnl_by_exit[reason] = pnl_by_exit.get(reason, 0.0) + pnl_f
        closed.append(
            {
                "timestamp": r["timestamp"],
                "symbol": r["symbol"],
                "strategy": r["strategy"],
                "exit_reason": reason,
                "execute_status": r["execute_status"],
                "realized_pnl": pnl_f,
            }
        )

    wins = [c for c in closed if (c.get("realized_pnl") or 0) > 0]
    losses = [c for c in closed if (c.get("realized_pnl") or 0) < 0]
    win_pnls = sum(c["realized_pnl"] for c in wins if c.get("realized_pnl") is not None)
    loss_pnls = abs(
        sum(c["realized_pnl"] for c in losses if c.get("realized_pnl") is not None)
    )
    profit_factor = (win_pnls / loss_pnls) if loss_pnls > 1e-9 else (float("inf") if win_pnls > 0 else 0.0)
    closed_with_pnl = [c for c in closed if c.get("realized_pnl") is not None]
    win_rate = len(wins) / len(closed_with_pnl) if closed_with_pnl else 0.0

    max_dd = 0.0
    if dd_rows:
        max_dd = abs(min(v for _, v in dd_rows))

    promo = {
        "closed_trades": len(closed_with_pnl),
        "win_rate": win_rate,
        "profit_factor": profit_factor if profit_factor != float("inf") else 99.0,
        "max_drawdown_pct": max_dd / 100.0,
        "pass_closed_trades": len(closed_with_pnl) >= PROMOTION_THRESHOLDS["min_closed_trades"],
        "pass_win_rate": win_rate >= PROMOTION_THRESHOLDS["min_win_rate"],
        "pass_profit_factor": (
            profit_factor >= PROMOTION_THRESHOLDS["min_profit_factor"]
            if profit_factor != float("inf")
            else True
        ),
        "pass_drawdown": (max_dd / 100.0) <= PROMOTION_THRESHOLDS["max_drawdown_pct"],
    }
    promo["pass_all"] = all(
        promo[k]
        for k in (
            "pass_closed_trades",
            "pass_win_rate",
            "pass_profit_factor",
            "pass_drawdown",
        )
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "profile": prof,
        "days": days,
        "cutoff_iso": cutoff,
        "by_action": dict(by_action),
        "by_execute_status": dict(by_execute),
        "equity_series": equity_rows,
        "drawdown_series": dd_rows,
        "closed_trades": closed[-50:],
        "exit_reason_counts": dict(exit_reason_counts),
        "pnl_by_exit_reason": {k: v for k, v in pnl_by_exit.items()},
        "promotion_metrics": promo,
        "thresholds": PROMOTION_THRESHOLDS,
    }
