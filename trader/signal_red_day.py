"""Per-ticker day return: prior close → last. Red = negative."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from alpaca.data.enums import DataFeed
from alpaca.data.requests import StockSnapshotRequest

from trader.alpaca_runtime import data_client


@dataclass(frozen=True)
class DaySignal:
    symbol: str
    last: float | None
    prev_close: float | None
    day_return: float | None
    is_red: bool
    error: str | None = None


def _f(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def day_signal(symbol: str) -> DaySignal:
    """Return day return for ``symbol`` using IEX snapshot (prev daily vs last)."""
    sym = symbol.strip().upper()
    try:
        out = data_client().get_stock_snapshot(
            StockSnapshotRequest(symbol_or_symbols=sym, feed=DataFeed.IEX)
        )
    except Exception as e:
        return DaySignal(sym, None, None, None, False, error=str(e))

    snap = out.get(sym) if isinstance(out, dict) else out
    if snap is None:
        return DaySignal(sym, None, None, None, False, error="no_snapshot")

    prev = getattr(snap, "previous_daily_bar", None)
    daily = getattr(snap, "daily_bar", None)
    trade = getattr(snap, "latest_trade", None)

    prev_close = _f(getattr(prev, "close", None)) if prev is not None else None
    last = _f(getattr(trade, "price", None)) if trade is not None else None
    if last is None and daily is not None:
        last = _f(getattr(daily, "close", None))

    if prev_close is None or prev_close <= 0 or last is None:
        return DaySignal(
            sym, last, prev_close, None, False, error="missing_prices"
        )

    day_ret = (last - prev_close) / prev_close
    return DaySignal(
        symbol=sym,
        last=last,
        prev_close=prev_close,
        day_return=day_ret,
        is_red=day_ret < 0.0,
    )


def is_red(symbol: str) -> bool:
    return day_signal(symbol).is_red
