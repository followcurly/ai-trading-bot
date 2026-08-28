"""Equity-curve metrics for backtests."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = (equity - peak) / peak.replace(0, np.nan)
    return float(dd.min()) if len(dd) else 0.0


def total_return(equity: pd.Series) -> float:
    if len(equity) < 2:
        return 0.0
    start = float(equity.iloc[0])
    end = float(equity.iloc[-1])
    if start <= 0:
        return 0.0
    return (end / start) - 1.0


def cagr(equity: pd.Series) -> float:
    if len(equity) < 2:
        return 0.0
    start = float(equity.iloc[0])
    end = float(equity.iloc[-1])
    if start <= 0 or end <= 0:
        return 0.0
    days = max((equity.index[-1] - equity.index[0]).days, 1)
    years = days / 365.25
    if years <= 0:
        return 0.0
    return float((end / start) ** (1.0 / years) - 1.0)


def slice_metrics(equity: pd.Series) -> dict[str, float]:
    return {
        "total_return": total_return(equity),
        "max_drawdown": max_drawdown(equity),
        "cagr": cagr(equity),
        "end_equity": float(equity.iloc[-1]) if len(equity) else 0.0,
        "start_equity": float(equity.iloc[0]) if len(equity) else 0.0,
        "n_days": int(len(equity)),
    }


def compute_report(
    strategy_eq: pd.Series,
    spy_eq: pd.Series,
    trades: list[dict[str, Any]],
    *,
    oos_frac: float = 0.30,
) -> dict[str, Any]:
    """Full / IS / OOS metrics. oos_frac = last fraction of timeline."""
    strategy_eq = strategy_eq.sort_index()
    spy_eq = spy_eq.reindex(strategy_eq.index).ffill()
    n = len(strategy_eq)
    split_i = max(1, int(n * (1.0 - oos_frac)))
    if split_i >= n:
        split_i = max(1, n - 1)
    split_date = strategy_eq.index[split_i]

    full_s = slice_metrics(strategy_eq)
    full_b = slice_metrics(spy_eq)
    is_s = slice_metrics(strategy_eq.iloc[:split_i])
    is_b = slice_metrics(spy_eq.iloc[:split_i])
    oos_s = slice_metrics(strategy_eq.iloc[split_i:])
    oos_b = slice_metrics(spy_eq.iloc[split_i:])

    # Per-lot mark PnL at end (buy-hold): win if end value > cost
    wins = 0
    lots = 0
    last_px: dict[str, float] = {}
    # approximate: use last strategy day closes from trades' symbols — caller may pass end prices on trades
    for t in trades:
        cost = float(t.get("notional") or 0) + float(t.get("cost") or 0)
        end_val = t.get("end_value")
        if end_val is None:
            continue
        lots += 1
        if float(end_val) > cost:
            wins += 1

    return {
        "full": {
            **full_s,
            "spy_total_return": full_b["total_return"],
            "vs_spy": full_s["total_return"] - full_b["total_return"],
        },
        "is": {
            **is_s,
            "spy_total_return": is_b["total_return"],
            "vs_spy": is_s["total_return"] - is_b["total_return"],
        },
        "oos": {
            **oos_s,
            "spy_total_return": oos_b["total_return"],
            "vs_spy": oos_s["total_return"] - oos_b["total_return"],
        },
        "split_date": split_date.strftime("%Y-%m-%d"),
        "trade_count": len([x for x in trades if x.get("side") in ("buy", "sell")]),
        "lot_wins": wins,
        "lot_count": lots,
        "win_rate": (wins / lots) if lots else None,
    }
