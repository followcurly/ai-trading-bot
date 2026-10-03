"""Alpaca bars + snapshot + indicators → market snapshot dict."""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
from alpaca.data.enums import DataFeed
from alpaca.data.requests import StockBarsRequest, StockSnapshotRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest

from trader.alpaca_runtime import data_client, trading_client
from trader.data_quality import build_data_quality
from trader.enrichment import enrich_snapshot
from trader.finnhub import fetch_earnings_calendar, fetch_recommendation
from trader.indicators import add_indicators
from trader.macro import enrich_macro
from trader.options_exec import option_atm_context, parse_occ_us_option_symbol
from trader.sentiment import fetch_fear_greed
from trader.sector import get_sector


def _is_option_on_underlying(sym: str, underlying: str) -> bool:
    """True only when ``sym`` is a valid OCC option whose root matches ``underlying``.

    The previous heuristic ``sym.startswith(underlying)`` false-matched related
    tickers — e.g. ``CAT`` against ``CATY`` / ``CATO`` / ``CATX`` — which would
    leak unrelated positions and orders into the per-symbol snapshot. Anchoring
    on the OCC root (``ROOT YYMMDD C/P STRIKE×1000``) eliminates that risk.
    """
    parsed = parse_occ_us_option_symbol(sym)
    if parsed is None:
        return False
    return str(parsed.get("underlying") or "").upper() == underlying.upper()


def _normalize_bars_df(bars: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if bars.empty:
        raise RuntimeError(f"No bar data for {symbol}")
    df = bars.reset_index()
    if "symbol" in df.columns:
        df = df[df["symbol"].str.upper() == symbol.upper()].copy()
        df = df.drop(columns=["symbol"], errors="ignore")
    ts_col = next(
        (c for c in ("timestamp", "timestamp_utc") if c in df.columns),
        None,
    )
    if ts_col:
        df = df.set_index(ts_col)
    else:
        df = df.set_index(df.columns[0])
    df.index = pd.to_datetime(df.index, utc=True)
    return df.sort_index()


def _position_for_symbol(symbol: str, positions: list) -> dict[str, Any]:
    """Return stock position for symbol. Also detects open options positions on the underlying."""
    sym = symbol.upper()
    stock_pos: dict[str, Any] = {"held": False}
    options_positions: list[dict[str, Any]] = []

    for p in positions:
        ps = (getattr(p, "symbol", None) or "").upper()
        if ps == sym:
            stock_pos = {
                "held": True,
                "qty": float(p.qty),
                "qty_available": float(getattr(p, "qty_available", p.qty) or p.qty),
                "avg_entry": float(p.avg_entry_price),
                "unrealized_pnl": float(p.unrealized_pl),
                "unrealized_pct": float(p.unrealized_plpc),
                "market_value": float(p.market_value),
                "current_price": float(p.current_price),
            }
        # Detect options on this underlying (e.g. "BITO260508C00011000"). OCC-anchored
        # so "CAT" does NOT pick up positions on "CATY" / "CATO" / "CATX".
        elif _is_option_on_underlying(ps, sym):
            try:
                options_positions.append({
                    "symbol": ps,
                    "qty": float(p.qty),
                    "avg_entry": float(p.avg_entry_price),
                    "unrealized_pnl": float(p.unrealized_pl),
                    "unrealized_pct": float(p.unrealized_plpc),
                    "market_value": float(p.market_value),
                })
            except Exception:
                pass

    if options_positions:
        stock_pos["open_options"] = options_positions
        stock_pos["open_options_contracts"] = int(sum(o["qty"] for o in options_positions))

    return stock_pos


def _enum_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _open_orders_for_symbol(tc, symbol: str) -> list[dict[str, Any]]:
    """Working / accepted / held orders for `symbol` AND its options, summarised for the brain.

    The brain uses this to avoid pitching duplicate exits (e.g. a SELL when a
    bracket take-profit limit is already reserving the shares).
    Also fetches all open orders and filters for the underlying to catch pending option orders.
    """
    sym = symbol.upper()
    out: list[dict[str, Any]] = []
    try:
        # Fetch all open orders and filter — catches both stock and options orders for this underlying
        all_orders = tc.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, limit=100))
    except Exception:
        return []
    for o in all_orders:
        try:
            o_sym = (getattr(o, "symbol", None) or "").upper()
            # Match exact symbol or OCC-anchored options on this underlying.
            # ``startswith`` would false-match unrelated tickers (CAT vs CATY/CATO);
            # the OCC check restricts to true option symbols only.
            if o_sym != sym and not _is_option_on_underlying(o_sym, sym):
                continue
            row = {
                "id": str(getattr(o, "id", "")),
                "symbol": o_sym,
                "side": _enum_str(getattr(o, "side", None)),
                "qty": float(getattr(o, "qty", None) or 0),
                "type": _enum_str(getattr(o, "order_type", None)),
                "order_class": _enum_str(getattr(o, "order_class", None)),
                "status": _enum_str(getattr(o, "status", None)),
                "tif": _enum_str(getattr(o, "time_in_force", None)),
            }
            lp = getattr(o, "limit_price", None)
            sp = getattr(o, "stop_price", None)
            if lp is not None:
                row["limit_price"] = float(lp)
            if sp is not None:
                row["stop_price"] = float(sp)
        except Exception:
            continue
        out.append(row)
    return out


def _alpaca_stock_snapshot(symbol: str) -> Any | None:
    """Latest trade / minute / daily bars from Market Data API (IEX for paper)."""
    try:
        out = data_client().get_stock_snapshot(
            StockSnapshotRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX)
        )
    except Exception:
        return None
    if isinstance(out, dict):
        return out.get(symbol)
    return None


def get_market_snapshot(symbol: str, regime: Any | None = None) -> dict:
    """Build the per-symbol market snapshot dict passed to the brain.

    When ``regime`` is set (cycle-level verdict from ``trader.regime``), the dict
    includes a ``market_regime`` subset (direction, conviction, rationale)
    identical for every symbol in that scan.
    """
    symbol = symbol.strip().upper()
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=120)
    raw = data_client().get_stock_bars(
        StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
            feed=DataFeed.IEX,
        )
    ).df
    bars = _normalize_bars_df(raw, symbol)
    bars = add_indicators(bars)
    last = bars.iloc[-1]
    daily_close = float(last["close"])

    snap_obj = _alpaca_stock_snapshot(symbol)
    live_price = daily_close
    bar_ts = str(bars.index[-1])
    vwap_snap: float | None = None
    volume_snap: float | None = None
    prev_close_snap: float | None = None

    if snap_obj is not None:
        lt = getattr(snap_obj, "latest_trade", None)
        mb = getattr(snap_obj, "minute_bar", None)
        db = getattr(snap_obj, "daily_bar", None)
        pdb = getattr(snap_obj, "previous_daily_bar", None)

        if lt is not None and getattr(lt, "price", None) is not None:
            live_price = float(lt.price)
            bar_ts = str(lt.timestamp)
        elif mb is not None:
            live_price = float(mb.close)
            bar_ts = str(mb.timestamp)
        elif db is not None:
            live_price = float(db.close)
            bar_ts = str(db.timestamp)

        if db is not None:
            vwap_snap = float(db.vwap) if getattr(db, "vwap", None) is not None else None
            volume_snap = float(db.volume) if getattr(db, "volume", None) is not None else None
        if pdb is not None and getattr(pdb, "close", None) is not None:
            prev_close_snap = float(pdb.close)

    if prev_close_snap is None and len(bars) >= 2:
        prev_close_snap = float(bars.iloc[-2]["close"])

    change_pct: float | None = None
    if prev_close_snap and prev_close_snap > 0:
        change_pct = round((live_price - prev_close_snap) / prev_close_snap, 6)

    vol_ratio = last.get("vol_ratio_20")
    vol_ratio_f = None
    if vol_ratio is not None and pd.notna(vol_ratio):
        try:
            vol_ratio_f = round(float(vol_ratio), 4)
        except (TypeError, ValueError):
            pass

    tc = trading_client()
    account = tc.get_account()
    positions = tc.get_all_positions()

    last_equity = float(getattr(account, "last_equity", None) or account.equity)
    equity_f = float(account.equity)
    daytrade_count = int(getattr(account, "daytrade_count", 0) or 0)
    pdt_flag = bool(getattr(account, "pattern_day_trader", False))

    pos = _position_for_symbol(symbol, positions)
    open_orders = _open_orders_for_symbol(tc, symbol)

    # Build portfolio-wide context so the brain knows what's already on the book
    portfolio_positions: list[dict[str, Any]] = []
    for p in positions:
        try:
            portfolio_positions.append({
                "symbol": (getattr(p, "symbol", None) or "").upper(),
                "qty": float(p.qty),
                "avg_entry": float(p.avg_entry_price),
                "unrealized_pnl": float(p.unrealized_pl),
                "market_value": float(p.market_value),
            })
        except Exception:
            pass

    def _ind_float(key: str, digits: int, default=None):
        v = last.get(key)
        if v is None or (isinstance(v, float) and pd.isna(v)):
            return default
        try:
            return round(float(v), digits)
        except (TypeError, ValueError):
            return default

    snap = {
        "symbol": symbol,
        "price": live_price,
        "timestamp": bar_ts,
        "position": pos,
        "open_orders": open_orders,
        "portfolio_context": {
            "total_positions": len(positions),
            "positions": portfolio_positions,
        },
        "indicators": {
            "rsi_14": round(float(last.get("RSI_14", 50)), 2),
            "macd": round(float(last.get("MACD_12_26_9", 0)), 4),
            "macd_signal": round(float(last.get("MACDs_12_26_9", 0)), 4),
            "ema_20": round(float(last.get("EMA_20", last["close"])), 2),
            "ema_50": round(float(last.get("EMA_50", last["close"])), 2),
            "atr_14": round(float(last.get("ATRr_14", 1)), 4),
            "adx_14": _ind_float("ADX_14", 2),
            "stoch_rsi_k": _ind_float("StochRSI_k", 2),
            "stoch_rsi_d": _ind_float("StochRSI_d", 2),
            "obv": _ind_float("OBV", 0),
            "bb_upper": round(float(last.get("BBU_20_2.0", last["close"])), 2),
            "bb_lower": round(float(last.get("BBL_20_2.0", last["close"])), 2),
            "volume_ratio": vol_ratio_f,
            "vwap": round(vwap_snap, 4) if vwap_snap is not None else None,
            "volume": round(volume_snap, 2) if volume_snap is not None else None,
            "prev_close": round(prev_close_snap, 4) if prev_close_snap is not None else None,
            "change_pct": change_pct,
            "daily_bar_close": round(daily_close, 4),
        },
        "options": {},
        "news_sentiment": "neutral",
        "earnings_days_away": None,
        "account": {
            "equity": equity_f,
            "cash": float(account.cash),
            "daily_pnl": equity_f - last_equity,
            "open_positions": len(positions),
            "daytrade_count": daytrade_count,
            "pattern_day_trader": pdt_flag,
        },
    }
    enrich_snapshot(symbol, snap)
    try:
        enrich_macro(snap)
    except Exception:
        pass
    try:
        oc = option_atm_context(symbol, live_price)
        if oc:
            snap["options"] = oc
    except Exception:
        pass

    # --- additional signal layers (all best-effort, omit on failure / missing key) ---
    try:
        fg = fetch_fear_greed()
        if fg:
            snap["fear_greed"] = fg
    except Exception:
        pass
    try:
        cal = fetch_earnings_calendar()
        if cal:
            snap["earnings_calendar"] = cal
    except Exception:
        pass
    try:
        rec = fetch_recommendation(symbol)
        if rec:
            snap["analyst_recommendation"] = rec
    except Exception:
        pass

    _sector_off = os.getenv("SECTOR_LOOKUP_DISABLE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    pc = snap.get("portfolio_context") or {}
    positions_list = list(pc.get("positions") or [])
    eq_f = float(snap.get("account", {}).get("equity") or 0)
    sector_mv: dict[str, float] = {}
    if not _sector_off:
        for p in positions_list:
            psym = str(p.get("symbol") or "")
            sec = get_sector(psym) or "Unknown"
            try:
                mv = float(p.get("market_value") or 0)
            except (TypeError, ValueError):
                mv = 0.0
            sector_mv[sec] = sector_mv.get(sec, 0.0) + mv
        snap["symbol_sector"] = get_sector(symbol)
    else:
        snap["symbol_sector"] = None
    if eq_f > 0:
        pc["sector_notional_pct"] = {k: v / eq_f for k, v in sector_mv.items()}
    else:
        pc["sector_notional_pct"] = {}
    snap["portfolio_context"] = pc

    snap["data_quality"] = build_data_quality(snap)
    if regime is not None:
        from trader.regime import snapshot_market_regime_field

        mr = snapshot_market_regime_field(regime)
        if mr is not None:
            snap["market_regime"] = mr
    return snap
