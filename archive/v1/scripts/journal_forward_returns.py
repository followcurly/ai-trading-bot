#!/usr/bin/env python3
"""Offline forward returns for journal BUY long_stock rows (research only; not live trading).

Uses yfinance closes vs entry price from `logic_json.snapshot_excerpt.price`.
Horizons are calendar-day approximations (not strict RTH bars).

Usage (from repo root, with venv activated):
  PYTHONPATH=. python scripts/journal_forward_returns.py
  PYTHONPATH=. python scripts/journal_forward_returns.py --db /path/to/trades.db --format md
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Repo root on sys.path when PYTHONPATH=.
from trader.config import DB_PATH


def _parse_ts(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def _entry_price(logic_json: str | None) -> float | None:
    if not logic_json:
        return None
    try:
        d = json.loads(logic_json)
    except json.JSONDecodeError:
        return None
    ex = d.get("snapshot_excerpt") or {}
    p = ex.get("price")
    try:
        v = float(p)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def _vix_regime(logic_json: str | None) -> str:
    if not logic_json:
        return ""
    try:
        d = json.loads(logic_json)
    except json.JSONDecodeError:
        return ""
    ex = d.get("snapshot_excerpt") or {}
    vix = ex.get("vix") or {}
    r = vix.get("regime")
    return str(r).strip().lower() if r else ""


def _confidence_bucket(c: float | None) -> str:
    if c is None:
        return "unknown"
    if c < 0.65:
        return "<0.65"
    if c < 0.75:
        return "0.65-0.75"
    if c < 0.85:
        return "0.75-0.85"
    return ">=0.85"


def _forward_return_pct(symbol: str, start: datetime, horizon_days: int) -> float | None:
    try:
        import yfinance as yf
    except ImportError:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    start_d = start.date()
    end_d = start_d + timedelta(days=horizon_days + 15)
    t = yf.Ticker(symbol)
    hist = t.history(start=start_d, end=end_d, auto_adjust=True)
    if hist is None or hist.empty:
        return None
    closes = hist["Close"].dropna()
    if closes.empty:
        return None
    # First close on/after entry date
    idx = closes.index
    entry_close = None
    for i in range(len(idx)):
        bar_d = idx[i].date() if hasattr(idx[i], "date") else idx[i]
        if bar_d >= start_d:
            entry_close = float(closes.iloc[i])
            break
    if entry_close is None or entry_close <= 0:
        return None
    target_d = start_d + timedelta(days=horizon_days)
    exit_close = None
    for i in range(len(idx)):
        bar_d = idx[i].date() if hasattr(idx[i], "date") else idx[i]
        if bar_d >= target_d:
            exit_close = float(closes.iloc[i])
            break
    if exit_close is None:
        exit_close = float(closes.iloc[-1])
    return (exit_close - entry_close) / entry_close * 100.0


def load_rows(db_path: Path) -> list[tuple]:
    if not db_path.is_file():
        raise SystemExit(f"DB not found: {db_path}")
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        """SELECT timestamp, symbol, action, strategy, confidence, logic_json
           FROM trades
           WHERE UPPER(COALESCE(action,'')) = 'BUY'
             AND LOWER(COALESCE(strategy,'')) = 'long_stock'
           ORDER BY id ASC"""
    ).fetchall()
    conn.close()
    return rows


def main() -> None:
    p = argparse.ArgumentParser(description="Forward returns from journal (long_stock BUY only)")
    p.add_argument("--db", type=Path, default=DB_PATH, help="Path to trades.db")
    p.add_argument(
        "--horizons",
        default="1,5,21",
        help="Comma-separated approximate forward calendar-day horizons",
    )
    p.add_argument("--format", choices=("md", "csv"), default="md")
    p.add_argument("--max-rows", type=int, default=0, help="Limit rows (0 = all)")
    args = p.parse_args()

    horizons = []
    for part in args.horizons.split(","):
        part = part.strip()
        if not part:
            continue
        horizons.append(int(part))
    if not horizons:
        horizons = [1, 5, 21]

    rows = load_rows(args.db)
    if args.max_rows > 0:
        rows = rows[: args.max_rows]

    # symbol -> list of (bucket or regime, {h: ret})
    by_conf: dict[str, list[dict[int, float]]] = defaultdict(list)
    by_vix: dict[str, list[dict[int, float]]] = defaultdict(list)
    all_series: list[dict[int, float]] = []

    for ts_s, sym, _action, _strat, conf, logic in rows:
        ts = _parse_ts(str(ts_s))
        if ts is None:
            continue
        sym_u = str(sym or "").strip().upper()
        if not sym_u:
            continue
        bucket = _confidence_bucket(float(conf) if conf is not None else None)
        regime = _vix_regime(logic) or "unknown"
        series: dict[int, float] = {}
        for h in horizons:
            r = _forward_return_pct(sym_u, ts, h)
            if r is not None:
                series[h] = r
        if not series:
            continue
        by_conf[bucket].append(series)
        by_vix[regime].append(series)
        all_series.append(series)

    def summarize(name: str, groups: dict[str, list[dict[int, float]]]) -> None:
        print(f"\n## {name}\n")
        if args.format == "md":
            print("| group | n | " + " | ".join(f"{h}d %" for h in horizons) + " |")
            print("|---:|---:|" + "|".join([":---"] * len(horizons)) + "|")
        for gkey in sorted(groups.keys()):
            series_list = groups[gkey]
            if not series_list:
                continue
            means = []
            for h in horizons:
                vals = [s[h] for s in series_list if h in s]
                means.append(statistics.mean(vals) if vals else float("nan"))
            if args.format == "md":
                row = f"| {gkey} | {len(series_list)} | "
                row += " | ".join(
                    "-" if isinstance(m, float) and m != m else f"{m:.2f}" for m in means
                )
                row += " |"
                print(row)
            else:
                print(
                    gkey,
                    len(series_list),
                    *[
                        "" if isinstance(m, float) and m != m else f"{m:.4f}"
                        for m in means
                    ],
                    sep=",",
                )

    print(f"# Journal forward returns (long_stock BUY)\n")
    print(f"- DB: `{args.db}`")
    print(f"- Rows loaded: {len(rows)} with computable horizons: {len(all_series)}")
    print(f"- Horizons (calendar days, approximate): {horizons}")
    print(f"- Assumes yfinance adjusted closes; survivorship and lookahead apply.")

    if args.format == "md":
        summarize("By confidence bucket", by_conf)
        summarize("By VIX regime (from snapshot excerpt)", by_vix)
    else:
        summarize("confidence", by_conf)
        summarize("vix_regime", by_vix)

    if not all_series:
        print("\nNo rows could be evaluated (missing history or DB empty).")


if __name__ == "__main__":
    main()
