"""Finnhub signals: earnings calendar (global) + per-symbol recommendation trend.

Free key from https://finnhub.io (60 req/min). Without `FINNHUB_API_KEY` set, all
helpers return None and the snapshot just omits the `finnhub` block — same pattern as FRED.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import date, timedelta
from typing import Any

import requests

log = logging.getLogger("trader.finnhub")

_BASE = "https://finnhub.io/api/v1"

_CAL_TTL = float(os.getenv("FINNHUB_CAL_TTL_SEC", "21600"))  # 6h
_REC_TTL = float(os.getenv("FINNHUB_REC_TTL_SEC", "21600"))  # 6h

_CAL_CACHE: tuple[float, list[dict[str, Any]] | None] | None = None
_REC_CACHE: dict[str, tuple[float, dict[str, Any] | None]] = {}


def _now() -> float:
    return time.monotonic()


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def _key() -> str:
    return (os.getenv("FINNHUB_API_KEY") or "").strip()


def _get(path: str, params: dict[str, Any], timeout: float) -> Any | None:
    key = _key()
    if not key:
        return None
    try:
        r = requests.get(
            f"{_BASE}{path}",
            params={**params, "token": key},
            timeout=timeout,
            headers={"User-Agent": "ai-trading-bot/1.0"},
        )
        if r.status_code == 429:
            return None
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def fetch_earnings_calendar(days: int = 7) -> list[dict[str, Any]] | None:
    """Upcoming earnings within the next N days (global; cached 6h)."""
    if _truthy("FINNHUB_DISABLE") or not _key():
        return None
    global _CAL_CACHE
    now = _now()
    if _CAL_CACHE and now - _CAL_CACHE[0] < _CAL_TTL:
        return _CAL_CACHE[1]

    today = date.today()
    body = _get(
        "/calendar/earnings",
        {"from": today.isoformat(), "to": (today + timedelta(days=days)).isoformat()},
        timeout=float(os.getenv("FINNHUB_TIMEOUT_SEC", "10")),
    )
    items = (body or {}).get("earningsCalendar") or []
    out: list[dict[str, Any]] = []
    for e in items:
        if not isinstance(e, dict):
            continue
        out.append(
            {
                "symbol": e.get("symbol"),
                "date": e.get("date"),
                "hour": e.get("hour"),
                "eps_estimate": e.get("epsEstimate"),
                "revenue_estimate": e.get("revenueEstimate"),
            }
        )
    _CAL_CACHE = (now, out or None)
    return out or None


def fetch_recommendation(symbol: str) -> dict[str, Any] | None:
    """Latest analyst recommendation trend for one symbol."""
    if _truthy("FINNHUB_DISABLE") or not _key():
        return None
    sym = (symbol or "").strip().upper()
    if not sym:
        return None
    now = _now()
    hit = _REC_CACHE.get(sym)
    if hit and now - hit[0] < _REC_TTL:
        return hit[1]

    rows = _get(
        "/stock/recommendation",
        {"symbol": sym},
        timeout=float(os.getenv("FINNHUB_TIMEOUT_SEC", "10")),
    )
    if not isinstance(rows, list) or not rows:
        _REC_CACHE[sym] = (now, None)
        return None
    latest = rows[0]
    out = {
        "period": latest.get("period"),
        "strong_buy": int(latest.get("strongBuy") or 0),
        "buy": int(latest.get("buy") or 0),
        "hold": int(latest.get("hold") or 0),
        "sell": int(latest.get("sell") or 0),
        "strong_sell": int(latest.get("strongSell") or 0),
    }
    _REC_CACHE[sym] = (now, out)
    return out
