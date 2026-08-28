"""Macro + cross-asset context: FRED + VIX.

TTL-caches the FRED yield curve / inflation / unemployment series and the
yfinance-derived VIX regime label; both attach to the snapshot for the brain
and the regime engine.
"""

from __future__ import annotations

import os
import time
from typing import Any

import requests
import yfinance as yf

_NOW = time.monotonic

_MACRO_CACHE: tuple[float, dict[str, Any] | None] | None = None
_VIX_CACHE: tuple[float, dict[str, Any] | None] | None = None

_MACRO_TTL = float(os.getenv("MACRO_TTL_SEC", "21600"))  # 6h
_VIX_TTL = float(os.getenv("VIX_TTL_SEC", "1800"))  # 30m

_FRED_URL = "https://api.stlouisfed.org/fred/series/observations"


def _fred_latest(api_key: str, series_id: str) -> float | None:
    try:
        r = requests.get(
            _FRED_URL,
            params={
                "series_id": series_id,
                "api_key": api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 1,
            },
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        obs = data.get("observations") or []
        if not obs:
            return None
        v = obs[0].get("value")
        if v in (None, ".", ""):
            return None
        return float(v)
    except Exception:
        return None


def _fred_cpi_yoy(api_key: str) -> float | None:
    """YoY % change in CPIAUCSL (last vs 12 months prior)."""
    try:
        r = requests.get(
            _FRED_URL,
            params={
                "series_id": "CPIAUCSL",
                "api_key": api_key,
                "file_type": "json",
                "sort_order": "desc",
                "limit": 13,
            },
            timeout=20,
        )
        r.raise_for_status()
        obs = (r.json().get("observations") or [])[:13]
        vals: list[float] = []
        for o in reversed(obs):
            v = o.get("value")
            if v in (None, ".", ""):
                continue
            vals.append(float(v))
        if len(vals) < 13:
            return None
        cur, prev_12 = vals[-1], vals[0]
        if prev_12 <= 0:
            return None
        return round((cur / prev_12) - 1.0, 6)
    except Exception:
        return None


def _fetch_macro_bundle() -> dict[str, Any] | None:
    key = (os.getenv("FRED_API_KEY") or "").strip()
    if not key:
        return None
    out: dict[str, Any] = {}
    d10 = _fred_latest(key, "DGS10")
    d2 = _fred_latest(key, "DGS2")
    t10y2y = _fred_latest(key, "T10Y2Y")
    unrate = _fred_latest(key, "UNRATE")
    cpi_yoy = _fred_cpi_yoy(key)
    if d10 is not None:
        out["dgs10"] = round(d10, 4)
    if d2 is not None:
        out["dgs2"] = round(d2, 4)
    if t10y2y is not None:
        out["t10y2y"] = round(t10y2y, 4)
    # UNRATE from FRED is e.g. 4.2 for 4.2% — store as decimal for model consistency
    if unrate is not None:
        out["unrate"] = round((unrate / 100.0) if unrate > 1.0 else unrate, 6)
    if cpi_yoy is not None:
        out["cpi_yoy"] = cpi_yoy
    return out if out else None


def _vix_bundle() -> dict[str, Any] | None:
    try:
        t = yf.Ticker("^VIX")
        h = t.history(period="5d", interval="1d")
        if h is None or h.empty:
            return None
        close = h["Close"]
        last = float(close.iloc[-1])
        prev = float(close.iloc[-2]) if len(close) > 1 else last
        chg = (last - prev) / prev if prev > 0 else 0.0
        if last < 15:
            regime = "calm"
        elif last < 25:
            regime = "normal"
        elif last < 35:
            regime = "elevated"
        else:
            regime = "stress"
        return {
            "level": round(last, 2),
            "change_pct": round(chg, 6),
            "regime": regime,
        }
    except Exception:
        return None


def enrich_macro(snapshot: dict) -> None:
    """Mutate snapshot with macro + vix keys (best-effort)."""
    if os.getenv("MACRO_DISABLE", "").strip().lower() in ("1", "true", "yes", "on"):
        return

    global _MACRO_CACHE, _VIX_CACHE

    now = _NOW()

    if _MACRO_CACHE and now - _MACRO_CACHE[0] < _MACRO_TTL:
        macro = _MACRO_CACHE[1]
    else:
        macro = _fetch_macro_bundle()
        _MACRO_CACHE = (now, macro)
    if macro:
        snapshot["macro"] = macro

    if _VIX_CACHE and now - _VIX_CACHE[0] < _VIX_TTL:
        vix = _VIX_CACHE[1]
    else:
        vix = _vix_bundle()
        _VIX_CACHE = (now, vix)
    if vix:
        snapshot["vix"] = vix
