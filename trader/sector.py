"""Best-effort equity sector labels (yfinance), TTL-cached per symbol."""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

log = logging.getLogger("trader.sector")

_TTL = float(os.getenv("SECTOR_CACHE_TTL_SEC", "86400"))
_CACHE: dict[str, tuple[float, str | None]] = {}


def underlying_equity_ticker(position_symbol: str) -> str:
    """Strip OCC option suffix so 'AAPL240119C00190000' -> 'AAPL'."""
    s = (position_symbol or "").strip().upper()
    if not s:
        return ""
    m = re.match(r"^([A-Z]{1,6})(\d{6})([CP])\d{8}$", s)
    if m:
        return m.group(1)
    return s


def get_sector(symbol: str) -> str | None:
    """Return GICS-style sector string or None if unknown."""
    sym = underlying_equity_ticker(symbol)
    if not sym:
        return None
    now = time.monotonic()
    hit = _CACHE.get(sym)
    if hit and now - hit[0] < _TTL:
        return hit[1]
    sector: str | None = None
    try:
        import yfinance as yf

        info: dict[str, Any] = yf.Ticker(sym).info or {}
        raw = info.get("sector")
        if isinstance(raw, str) and raw.strip():
            sector = raw.strip()
    except Exception:
        log.debug("sector_lookup_failed symbol=%s", sym, exc_info=True)
    _CACHE[sym] = (now, sector)
    return sector


def clear_sector_cache() -> None:
    _CACHE.clear()
