"""Daily bar fetch + disk cache for backtests (Alpaca IEX; split or all)."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable

import pandas as pd
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

import os

from alpaca.data.enums import CorporateActionsType
from alpaca.data.historical.corporate_actions import CorporateActionsClient
from alpaca.data.requests import CorporateActionsRequest

from trader.alpaca_runtime import data_client
from trader.config import REPO_ROOT

CACHE_DIR = REPO_ROOT / "data" / "backtests" / "cache"


def _alpaca_keys() -> tuple[str, str]:
    key = (
        os.getenv("ALPACA_API_KEY")
        or os.getenv("APCA_API_KEY_ID")
        or os.getenv("ALPACA_KEY_ID")
        or ""
    ).strip()
    sec = (
        os.getenv("ALPACA_SECRET_KEY")
        or os.getenv("APCA_API_SECRET_KEY")
        or os.getenv("ALPACA_API_SECRET")
        or ""
    ).strip()
    return key, sec


def fetch_cash_dividends(
    symbols: Iterable[str],
    start: date,
    end: date,
) -> list[dict]:
    """Cash dividends (ex_date, symbol, rate $/share) via Alpaca corporate actions."""
    syms = sorted({s.strip().upper() for s in symbols if s and str(s).strip()})
    if not syms:
        return []
    key, sec = _alpaca_keys()
    if not key or not sec:
        return []
    client = CorporateActionsClient(key, sec)
    raw = client.get_corporate_actions(
        CorporateActionsRequest(
            symbols=syms,
            types=[CorporateActionsType.CASH_DIVIDEND],
            start=start,
            end=end,
        )
    )
    rows = []
    data = getattr(raw, "data", None) or {}
    if isinstance(data, dict):
        items = data.get("cash_dividends") or []
    else:
        items = []
    for item in items:
        sym = getattr(item, "symbol", None) or (item.get("symbol") if isinstance(item, dict) else None)
        rate = getattr(item, "rate", None) if not isinstance(item, dict) else item.get("rate")
        ex = getattr(item, "ex_date", None) if not isinstance(item, dict) else item.get("ex_date")
        if not sym or rate is None or ex is None:
            continue
        if hasattr(ex, "isoformat"):
            ex_s = ex.isoformat()
        else:
            ex_s = str(ex)[:10]
        rows.append({"symbol": str(sym).upper(), "ex_date": ex_s, "rate": float(rate)})
    rows.sort(key=lambda r: (r["ex_date"], r["symbol"]))
    return rows



def _cache_path(
    symbol: str,
    start: date,
    end: date,
    history_pad_days: int = 14,
    *,
    adjustment: str = "split",
) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    adj = (adjustment or "split").strip().lower()
    return CACHE_DIR / f"{symbol}_{start.isoformat()}_{end.isoformat()}_pad{history_pad_days}_{adj}.pkl"


def fetch_daily_bars(
    symbols: Iterable[str],
    start: date,
    end: date,
    *,
    use_cache: bool = True,
    progress: Callable[[str, str], None] | None = None,
    history_pad_days: int = 14,
    adjustment: str = "split",
) -> dict[str, pd.DataFrame]:
    """Return {SYM: DataFrame indexed by date with open/high/low/close/volume}.

    adjustment:
      - "split" (default): split-only — cash dividends are NOT in the price path
        (so the engine can park yield as idle cash without double-counting).
      - "all": split + dividend adjusted (legacy total-return path).
    """
    adj_key = (adjustment or "split").strip().lower()
    if adj_key in ("all", "div", "dividend", "total"):
        adj_enum = Adjustment.ALL
        adj_key = "all"
    else:
        adj_enum = Adjustment.SPLIT
        adj_key = "split"
    syms = sorted({s.strip().upper() for s in symbols if s and str(s).strip()})
    out: dict[str, pd.DataFrame] = {}
    missing: list[str] = []
    pad = max(14, int(history_pad_days))

    for sym in syms:
        path = _cache_path(sym, start, end, pad, adjustment=adj_key)
        if use_cache and path.exists():
            df = pd.read_pickle(path)
            df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
            out[sym] = df
            if progress:
                progress(sym, "cache")
        else:
            missing.append(sym)

    if not missing:
        return out

    # Pad start for prior closes / SMA warm-up
    start_dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc) - timedelta(
        days=pad
    )
    end_dt = datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc)

    # Fetch one symbol at a time so progress UI / CLI can report cache misses cleanly
    for sym in missing:
        raw = data_client().get_stock_bars(
            StockBarsRequest(
                symbol_or_symbols=sym,
                timeframe=TimeFrame.Day,
                start=start_dt,
                end=end_dt,
                feed=DataFeed.IEX,
                adjustment=adj_enum,
            )
        )
        df_all = raw.df
        if df_all is None or df_all.empty:
            raise RuntimeError(f"No daily bars for {sym} in {start}..{end}")

        flat = df_all.reset_index()
        if "symbol" in flat.columns:
            flat = flat[flat["symbol"] == sym]
        if flat.empty:
            raise RuntimeError(f"No daily bars for {sym} in {start}..{end}")
        flat["date"] = (
            pd.to_datetime(flat["timestamp"], utc=True).dt.tz_convert(None).dt.normalize()
        )
        flat = flat.set_index("date").sort_index()
        flat = flat[~flat.index.duplicated(keep="last")]
        cols = [c for c in ("open", "high", "low", "close", "volume") if c in flat.columns]
        frame = flat[cols].astype(float)
        path = _cache_path(sym, start, end, pad, adjustment=adj_key)
        frame.to_pickle(path)
        path.with_suffix(".json").write_text(
            json.dumps(
                {
                    "symbol": sym,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "rows": len(frame),
                    "adjustment": adj_key,
                }
            ),
            encoding="utf-8",
        )
        out[sym] = frame
        if progress:
            progress(sym, "fetch")

    return out


def align_calendar(
    bars: dict[str, pd.DataFrame],
    start: date,
    end: date,
    *,
    master: str = "SPY",
) -> pd.DatetimeIndex:
    """Trading days from the master series (default SPY) clipped to [start, end]."""
    start_ts = pd.Timestamp(start)
    end_ts = pd.Timestamp(end)
    if master not in bars:
        raise RuntimeError(f"master series {master} missing from bars")
    idx = bars[master].index
    idx = idx[(idx >= start_ts) & (idx <= end_ts)]
    if len(idx) == 0:
        raise RuntimeError("Empty aligned calendar")
    return idx.sort_values()
