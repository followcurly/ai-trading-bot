"""Dynamic per-cycle watchlist — Alpaca screener (most actives) + small benchmark anchors.

Replaces the static `TRADING_WATCHLIST` env. The bot scans whatever is liquid right now,
plus an optional anchor set (e.g. `SPY,QQQ`) for regime context. TTL-cached.

Envs:
- WATCHLIST_SCREENER_SIZE   (int, default 20)     — max symbols from screener after filters; held underlyings appended after (uncapped).
- WATCHLIST_SIZE            (int, legacy)         — used only when WATCHLIST_SCREENER_SIZE unset.
- WATCHLIST_ANCHORS       (csv,  default "SPY,QQQ") — always-included regime context; "" = none.
- WATCHLIST_MIN_PRICE     (float, default 5.0)     — used when WATCHLIST_ENFORCE_MIN_PRICE=true.
- WATCHLIST_ENFORCE_MIN_PRICE (bool, default true) — Alpaca last trade vs MIN_PRICE (off = penny experiments).
- WATCHLIST_MAX_SPREAD_PCT (float, optional)       — drop if (ask-bid)/mid exceeds this (IEX quotes).
- WATCHLIST_MIN_AVG_DOLLAR_VOL (float, optional)  — min avg_volume×price from yfinance (cached).
- WATCHLIST_MIN_MARKET_CAP (float, optional)      — min market cap from yfinance (cached).
- WATCHLIST_DENY_SYMBOLS  (csv)                    — hard drop list.
- WATCHLIST_DENY_PREFIXES (csv)                    — drop symbols starting with any prefix.
- WATCHLIST_TTL_SEC       (float, default 600)     — cache lifetime (10m).
- WATCHLIST_DISABLE       (bool, default false)    — emergency switch back to anchors-only.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from alpaca.common.exceptions import APIError
from alpaca.data.enums import DataFeed
from alpaca.data.requests import MostActivesRequest, StockLatestQuoteRequest, StockLatestTradeRequest

from trader.alpaca_runtime import data_client, screener_client, trading_client
from trader.config import (
    WATCHLIST_INCLUDE_OPEN_POSITIONS,
    WATCHLIST_SCREENER_SIZE,
    WATCHLIST_STATIC_OVERRIDE,
)
from trader.regime import MarketRegime, regime_bearish_inject_symbols
from trader.options_exec import parse_occ_us_option_symbol

log = logging.getLogger("trader.watchlist")

_TTL = float(os.getenv("WATCHLIST_TTL_SEC", "600"))
_CACHE: tuple[float, str, list[str]] | None = None
_YF_FUND_TTL = float(os.getenv("WATCHLIST_YF_CACHE_TTL_SEC", "3600"))
_YF_FUND_CACHE: dict[str, tuple[float, float | None, float | None]] = {}


def _csv_env(name: str, default: str) -> list[str]:
    raw = os.getenv(name, default)
    return [s.strip().upper() for s in (raw or "").split(",") if s.strip()]


def _min_price() -> float:
    try:
        return max(0.0, float(os.getenv("WATCHLIST_MIN_PRICE", "5.0")))
    except ValueError:
        return 5.0


def _is_disabled() -> bool:
    return os.getenv("WATCHLIST_DISABLE", "").strip().lower() in ("1", "true", "yes", "on")


def _env_float_opt(name: str) -> float | None:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _enforce_min_price() -> bool:
    """Default ON: filters sub-WATCHLIST_MIN_PRICE penny stocks from the brain.

    Prior to this default, names like EZGO ($0.05) reached the brain and produced
    bracket orders Alpaca rejected (``stop_price must be > 0``) because ATR
    exceeded the share price. Flip ``WATCHLIST_ENFORCE_MIN_PRICE=false`` to opt
    out for penny-stock experiments.
    """
    raw = (os.getenv("WATCHLIST_ENFORCE_MIN_PRICE") or "true").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _deny_list_filter(symbols: list[str]) -> tuple[list[str], list[str]]:
    deny = {
        s.strip().upper()
        for s in os.getenv("WATCHLIST_DENY_SYMBOLS", "").split(",")
        if s.strip()
    }
    prefixes = [
        p.strip().upper()
        for p in os.getenv("WATCHLIST_DENY_PREFIXES", "").split(",")
        if p.strip()
    ]
    kept: list[str] = []
    drops: list[str] = []
    for s in symbols:
        u = s.upper()
        if deny and u in deny:
            drops.append(f"{u}:deny_symbol")
            continue
        if prefixes and any(u.startswith(p) for p in prefixes):
            drops.append(f"{u}:deny_prefix")
            continue
        kept.append(u)
    return kept, drops


def _yf_mcap_adv(sym: str) -> tuple[float | None, float | None]:
    now = time.monotonic()
    hit = _YF_FUND_CACHE.get(sym.upper())
    if hit and now - hit[0] < _YF_FUND_TTL:
        return hit[1], hit[2]
    mcap: float | None = None
    adv: float | None = None
    try:
        import yfinance as yf

        info = yf.Ticker(sym).info or {}
        raw_m = info.get("marketCap")
        if raw_m is not None:
            try:
                mcap = float(raw_m)
            except (TypeError, ValueError):
                pass
        avgv = info.get("averageVolume") or info.get("averageDailyVolume10Day")
        last = (
            info.get("regularMarketPrice")
            or info.get("currentPrice")
            or info.get("previousClose")
        )
        if avgv is not None and last is not None:
            try:
                adv = float(avgv) * float(last)
            except (TypeError, ValueError):
                pass
    except Exception:
        log.debug("watchlist_yf_fundamental_failed symbol=%s", sym, exc_info=True)
    _YF_FUND_CACHE[sym.upper()] = (now, mcap, adv)
    return mcap, adv


def _fundamental_filter(
    symbols: list[str],
    min_mcap: float | None,
    min_adv: float | None,
) -> tuple[list[str], list[str]]:
    if not symbols or (min_mcap is None and min_adv is None):
        return list(symbols), []
    kept: list[str] = []
    drops: list[str] = []
    for sym in symbols:
        mcap, adv = _yf_mcap_adv(sym)
        if min_mcap is not None and min_mcap > 0:
            if mcap is None or mcap < min_mcap:
                drops.append(f"{sym}:min_mcap")
                continue
        if min_adv is not None and min_adv > 0:
            if adv is None or adv < min_adv:
                drops.append(f"{sym}:min_avg_dollar_vol")
                continue
        kept.append(sym)
    return kept, drops


def _alpaca_liquidity_filter(
    symbols: list[str],
    min_price: float,
    max_spread_pct: float | None,
) -> tuple[list[str], list[str]]:
    if not symbols:
        return [], []
    need_quotes = max_spread_pct is not None and max_spread_pct > 0
    need_trades = min_price > 0
    if not need_quotes and not need_trades:
        return list(symbols), []
    dc = data_client()
    trade_map: dict[str, float] = {}
    quote_map: dict[str, tuple[float, float]] = {}
    try:
        if need_trades:
            tr = dc.get_stock_latest_trade(
                StockLatestTradeRequest(
                    symbol_or_symbols=symbols,
                    feed=DataFeed.IEX,
                )
            )
            for sym in symbols:
                u = sym.upper()
                obj = tr[u] if isinstance(tr, dict) else getattr(tr, u, None)
                if obj is None:
                    continue
                pr = getattr(obj, "price", None)
                if pr is not None:
                    try:
                        trade_map[u] = float(pr)
                    except (TypeError, ValueError):
                        pass
        if need_quotes:
            qt = dc.get_stock_latest_quote(
                StockLatestQuoteRequest(
                    symbol_or_symbols=symbols,
                    feed=DataFeed.IEX,
                )
            )
            for sym in symbols:
                u = sym.upper()
                obj = qt[u] if isinstance(qt, dict) else getattr(qt, u, None)
                if obj is None:
                    continue
                bid = getattr(obj, "bid_price", None)
                ask = getattr(obj, "ask_price", None)
                if bid is not None and ask is not None:
                    try:
                        b, a = float(bid), float(ask)
                        if b > 0 and a > 0:
                            quote_map[u] = (b, a)
                    except (TypeError, ValueError):
                        pass
    except Exception as e:
        log.warning("watchlist_alpaca_liquidity_skip: %s", e)
        return list(symbols), []

    kept: list[str] = []
    drops: list[str] = []
    for sym in symbols:
        u = sym.upper()
        if need_trades:
            px = trade_map.get(u)
            if px is None or px < min_price:
                drops.append(f"{u}:min_price")
                continue
        if need_quotes:
            pair = quote_map.get(u)
            if not pair:
                drops.append(f"{u}:no_quote_spread")
                continue
            bid, ask = pair
            mid = (bid + ask) / 2.0
            if mid <= 0:
                drops.append(f"{u}:bad_mid")
                continue
            sp = (ask - bid) / mid
            if sp > float(max_spread_pct):
                drops.append(f"{u}:wide_spread")
                continue
        kept.append(sym)
    return kept, drops


def _apply_watchlist_quality_filters(symbols: list[str]) -> tuple[list[str], list[str]]:
    """Return (filtered_symbols, drop_reasons). Fail-open on API errors inside sub-filters."""
    out, drops = _deny_list_filter(symbols)
    min_mcap = _env_float_opt("WATCHLIST_MIN_MARKET_CAP")
    min_adv = _env_float_opt("WATCHLIST_MIN_AVG_DOLLAR_VOL")
    out2, d2 = _fundamental_filter(out, min_mcap, min_adv)
    drops.extend(d2)

    min_px = _min_price() if _enforce_min_price() else 0.0
    max_spread = _env_float_opt("WATCHLIST_MAX_SPREAD_PCT")
    out3, d3 = _alpaca_liquidity_filter(out2, min_px, max_spread)
    drops.extend(d3)
    return out3, drops


def _open_position_underlyings() -> list[str]:
    """Underlyings of every open Alpaca position, OCC-decoded for options.

    Best-effort: any failure returns an empty list so the watchlist build never
    fails because of a transient Alpaca read. Quality filters do NOT run on these
    symbols — we already own them, so we always want Claude to evaluate them
    regardless of spread / min-price / market-cap rules. (E.g. a position can
    legitimately drop below MIN_PRICE; we still need to be able to SELL it.)
    """
    try:
        tc = trading_client()
        positions = tc.get_all_positions()
    except Exception:
        log.debug("watchlist_open_positions_fetch_failed", exc_info=True)
        return []
    out: list[str] = []
    seen: set[str] = set()
    for p in positions or []:
        raw = (getattr(p, "symbol", "") or "").strip().upper()
        if not raw:
            continue
        # Decode OCC option symbols (e.g. DGXX260515C00005000 -> DGXX); stocks pass through.
        parsed = parse_occ_us_option_symbol(raw)
        sym = parsed["underlying"].upper() if parsed else raw
        if sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    return out


def _fetch_most_actives(top: int) -> list[str]:
    """Pull top-N most active US equities by volume from Alpaca's screener."""
    try:
        sc = screener_client()
        resp = sc.get_most_actives(MostActivesRequest(top=top, by="volume"))
    except APIError as e:
        log.warning("screener_api_error: %s", e)
        return []
    except Exception:
        log.exception("screener_fetch_failed")
        return []

    items: list[Any] = []
    if hasattr(resp, "most_actives"):
        items = list(getattr(resp, "most_actives") or [])
    elif isinstance(resp, dict):
        items = list(resp.get("most_actives") or [])
    out: list[str] = []
    for it in items:
        sym = getattr(it, "symbol", None) if not isinstance(it, dict) else it.get("symbol")
        if isinstance(sym, str) and sym.strip():
            out.append(sym.strip().upper())
    return out


def _filter_tradable(candidates: list[str]) -> list[str]:
    """Keep only symbols Alpaca lists as tradable equities. Best-effort; failures pass through."""
    if not candidates:
        return []
    try:
        tc = trading_client()
    except Exception:
        return candidates
    kept: list[str] = []
    for sym in candidates:
        try:
            asset = tc.get_asset(sym)
            if getattr(asset, "tradable", False) and str(getattr(asset, "asset_class", "")).lower().endswith("us_equity"):
                kept.append(sym)
        except APIError:
            continue
        except Exception:
            kept.append(sym)
    return kept


def get_watchlist(force: bool = False, regime: MarketRegime | None = None) -> list[str]:
    """Return the current cycle's symbol universe.

    Order: anchors first (so benchmarks like SPY/QQQ always lead), optional bearish
    hedge tickers when ``regime`` is bearish with conviction above the configured
    threshold, then top movers by volume (capped at ``WATCHLIST_SCREENER_SIZE``
    after quality filters), then held-position underlyings (never capped). Cached
    for ``WATCHLIST_TTL_SEC`` (cache key includes regime fingerprint).
    """
    global _CACHE

    now = time.monotonic()
    regime_sig = regime.cache_key() if regime is not None else "none"
    if not force and _CACHE and now - _CACHE[0] < _TTL and _CACHE[1] == regime_sig:
        return list(_CACHE[2])

    # Open positions are always evaluated — quality filters do NOT apply (we
    # already own them; we MUST be able to SELL them). Computed once and merged
    # into both the static-override and dynamic-screener paths below.
    held = _open_position_underlyings() if WATCHLIST_INCLUDE_OPEN_POSITIONS else []

    # Hard env override beats the screener (kept for emergencies / lockdown).
    # Held underlyings still get appended so we can manage what we own even when
    # the operator pinned a static override list.
    if WATCHLIST_STATIC_OVERRIDE:
        ss = WATCHLIST_SCREENER_SIZE
        static_part = list(dict.fromkeys(WATCHLIST_STATIC_OVERRIDE))
        if ss > 0:
            static_part = static_part[:ss]
        final = list(dict.fromkeys([*static_part, *held]))
        _CACHE = (now, regime_sig, final)
        return list(final)

    anchors = _csv_env("WATCHLIST_ANCHORS", "SPY,QQQ")
    ss = WATCHLIST_SCREENER_SIZE
    inject = regime_bearish_inject_symbols(regime)

    if _is_disabled():
        merged = list(dict.fromkeys([*anchors, *inject, *held]))
        _CACHE = (now, regime_sig, merged)
        return list(merged)

    raw = (
        []
        if ss <= 0
        else _fetch_most_actives(top=max(ss * 2, 25))
    )

    seen: set[str] = set()
    ordered: list[str] = []
    for s in [*anchors, *inject, *raw]:
        if s and s not in seen:
            seen.add(s)
            ordered.append(s)

    filtered = _filter_tradable(ordered)
    quality, drop_log = _apply_watchlist_quality_filters(filtered)
    if drop_log:
        log.info(
            "watchlist_quality_dropped count=%d sample=%s",
            len(drop_log),
            drop_log[:12],
        )
    if ss <= 0:
        base = list(dict.fromkeys([*anchors, *inject, *quality, *filtered]))
    else:
        base = quality if quality else filtered
        base = base[:ss] if base else anchors[: min(len(anchors), ss)]

    rescue = [h for h in inject if h not in base]
    # Append held underlyings AFTER quality filtering so they can never be dropped.
    # Total length may exceed ``ss``; held names are never truncated.
    if held:
        final = list(dict.fromkeys([*base, *rescue, *held]))
    else:
        final = list(dict.fromkeys([*base, *rescue])) if rescue else base
    _CACHE = (now, regime_sig, final)
    log.info(
        "watchlist_built size=%d screener_cap=%d source=actives anchors=%s held_injected=%d sample=%s",
        len(final),
        ss,
        anchors,
        len(held),
        final[:6],
    )
    return list(final)
