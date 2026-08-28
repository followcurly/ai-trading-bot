"""Resolve Alpaca option contracts, ATM context for snapshots, and sizing."""

from __future__ import annotations

import math
import os
import re
from datetime import date, datetime, timedelta
from typing import Any

from alpaca.common.exceptions import APIError
from alpaca.data.enums import OptionsFeed
from alpaca.data.requests import (
    OptionChainRequest,
    OptionLatestQuoteRequest,
    OptionLatestTradeRequest,
    OptionSnapshotRequest,
)
from alpaca.trading.enums import AssetStatus, ContractType
from alpaca.trading.requests import GetOptionContractsRequest

from trader.alpaca_runtime import option_data_client, trading_client


def options_quote_feed() -> OptionsFeed:
    raw = os.getenv("ALPACA_OPTIONS_FEED", "indicative").strip().lower()
    if raw == "opra":
        return OptionsFeed.OPRA
    return OptionsFeed.INDICATIVE


def parse_expiry_date(raw: str | None) -> date | None:
    if not raw:
        return None
    s = str(raw).strip()[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_occ_us_option_symbol(osi: str | None) -> dict[str, Any] | None:
    """Parse Alpaca US equity option symbol (OCC-style), e.g. ``NOK260515C00013500``."""
    if not osi:
        return None
    s = str(osi).strip().upper()
    m = re.match(r"^(.+?)(\d{6})([CP])(\d{8})$", s)
    if not m:
        return None
    root, ymd, cp, strike_raw = m.groups()
    if not root:
        return None
    try:
        exp = datetime.strptime(ymd, "%y%m%d").date()
        strike = int(strike_raw, 10) / 1000.0
    except ValueError:
        return None
    if strike <= 0:
        return None
    strat = "long_call" if cp == "C" else "long_put"
    return {
        "underlying": root,
        "expiry": exp,
        "expiry_iso": exp.isoformat(),
        "is_call": cp == "C",
        "strategy": strat,
        "strike": strike,
    }


def contract_type_for_strategy(strategy: str) -> ContractType | None:
    if strategy == "long_call":
        return ContractType.CALL
    if strategy == "long_put":
        return ContractType.PUT
    return None


def fetch_tradable_contract(
    tc: Any,
    underlying: str,
    strategy: str,
    strike: float,
    exp: date,
):
    """Return Alpaca OptionContract or None."""
    ctype = contract_type_for_strategy(strategy)
    if ctype is None:
        return None
    sk = f"{strike:g}"
    req = GetOptionContractsRequest(
        underlying_symbols=[underlying.upper()],
        status=AssetStatus.ACTIVE,
        expiration_date=exp,
        type=ctype,
        strike_price_gte=sk,
        strike_price_lte=sk,
        limit=50,
    )
    try:
        resp = tc.get_option_contracts(req)
    except APIError:
        return None
    contracts = resp.option_contracts or []
    for c in contracts:
        if not c.tradable:
            continue
        if abs(float(c.strike_price) - float(strike)) > 0.01:
            continue
        if c.expiration_date != exp:
            continue
        return c
    return None


def _parse_close_price(contract) -> float | None:
    raw = getattr(contract, "close_price", None)
    if raw is None:
        return None
    try:
        v = float(raw)
        return v if v > 0 and not math.isnan(v) else None
    except (TypeError, ValueError):
        return None


def fetch_option_bid_ask(
    option_dc: Any,
    option_symbol: str,
) -> tuple[float | None, float | None]:
    """Return (bid_price, ask_price) for an option symbol or (None, None) on failure.

    Treats explicit zero quotes as None so callers can distinguish 'no quote'
    from 'real quote of zero' uniformly with bid > 0 / ask > 0 checks.
    """
    feed = options_quote_feed()
    try:
        quotes = option_dc.get_option_latest_quote(
            OptionLatestQuoteRequest(symbol_or_symbols=option_symbol, feed=feed)
        )
    except Exception:
        return None, None
    if not (isinstance(quotes, dict) and option_symbol in quotes):
        return None, None
    q = quotes[option_symbol]
    bp = float(getattr(q, "bid_price", 0) or 0) or None
    ap = float(getattr(q, "ask_price", 0) or 0) or None
    return bp, ap


def estimate_premium_per_share(
    option_dc: Any,
    option_symbol: str,
    contract,
) -> float | None:
    """Best-effort ask-side reference $/share (one option unit, not ×100)."""
    bp, ap = fetch_option_bid_ask(option_dc, option_symbol)
    if ap and ap > 0:
        return ap
    if bp and bp > 0:
        return bp
    feed = options_quote_feed()
    try:
        trades = option_dc.get_option_latest_trade(
            OptionLatestTradeRequest(symbol_or_symbols=option_symbol, feed=feed)
        )
    except Exception:
        trades = None
    if isinstance(trades, dict) and option_symbol in trades:
        t = trades[option_symbol]
        px = float(getattr(t, "price", 0) or 0)
        if px > 0:
            return px
    return _parse_close_price(contract)


def conviction_contract_cap(confidence: float) -> int:
    """Max option contracts from ``OPTIONS_SIZE_TIERS`` + floor + hard cap (before budget).

    When ``OPTIONS_SIZE_TIERS`` is empty (explicit disable or malformed), returns
    ``min(MAX_OPTIONS_CONTRACTS_FLOOR, MAX_OPTIONS_CONTRACTS_HARD)`` — legacy flat cap.
    """
    from trader.config import (
        MAX_OPTIONS_CONTRACTS_FLOOR,
        MAX_OPTIONS_CONTRACTS_HARD,
        OPTIONS_SIZE_TIERS,
    )

    try:
        conf = float(confidence)
    except (TypeError, ValueError):
        conf = 0.0
    if math.isnan(conf):
        conf = 0.0
    conf = max(0.0, min(1.0, conf))

    if not OPTIONS_SIZE_TIERS:
        return min(MAX_OPTIONS_CONTRACTS_FLOOR, MAX_OPTIONS_CONTRACTS_HARD)

    tier_cap = MAX_OPTIONS_CONTRACTS_FLOOR
    for t in OPTIONS_SIZE_TIERS:
        if conf >= t.threshold:
            tier_cap = max(tier_cap, t.max_contracts)
    return min(tier_cap, MAX_OPTIONS_CONTRACTS_HARD)


def contracts_qty_for_buy(
    equity: float,
    size_pct: float,
    premium_per_share: float,
    max_contracts: int,
) -> int | None:
    if equity <= 0 or size_pct <= 0 or premium_per_share <= 0 or max_contracts < 1:
        return None
    budget = equity * size_pct
    per_contract = premium_per_share * 100.0
    n = int(math.floor(budget / per_contract))
    if n < 1:
        return None
    return min(max_contracts, n)


def _opt_snap_fields(sn: Any) -> dict[str, Any]:
    """Serialize OptionsSnapshot for Claude / journal."""
    out: dict[str, Any] = {}
    iv = getattr(sn, "implied_volatility", None)
    if iv is not None:
        try:
            out["iv"] = round(float(iv), 6)
        except (TypeError, ValueError):
            pass
    q = getattr(sn, "latest_quote", None)
    bid = ask = None
    if q is not None:
        try:
            bid = float(getattr(q, "bid_price", 0) or 0) or None
            ask = float(getattr(q, "ask_price", 0) or 0) or None
        except (TypeError, ValueError):
            pass
    mid: float | None = None
    if bid and ask and bid > 0 and ask > 0:
        mid = round((ask + bid) / 2.0, 4)
    elif ask and ask > 0:
        mid = round(ask, 4)
    elif bid and bid > 0:
        mid = round(bid, 4)
    t = getattr(sn, "latest_trade", None)
    if mid is None and t is not None:
        try:
            px = float(getattr(t, "price", 0) or 0)
            if px > 0:
                mid = round(px, 4)
        except (TypeError, ValueError):
            pass
    if mid is not None:
        out["mid"] = mid
    if bid:
        out["bid"] = round(bid, 4)
    if ask:
        out["ask"] = round(ask, 4)
    return out


def option_atm_context(underlying: str, ref_price: float) -> dict[str, Any] | None:
    """Nearest listed expiry + ATM±N strikes: call/put IV and mid from option chain snapshot."""
    if ref_price <= 0 or math.isnan(ref_price):
        return None
    if os.getenv("OPTIONS_SNAPSHOT_DISABLE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return None

    today = date.today()
    end = today + timedelta(days=90)
    spacing = float(os.getenv("OPTIONS_ATM_SPACING", "1.0"))
    n_ring = max(0, int(os.getenv("OPTIONS_ATM_STRIKES", "1")))
    try:
        min_dte = int(os.getenv("MIN_OPTION_DTE", "7") or 7)
    except ValueError:
        min_dte = 7
    min_dte = max(0, min_dte)
    lo = max(1e-6, ref_price - spacing * (n_ring + 4))
    hi = ref_price + spacing * (n_ring + 4)

    tc = trading_client()
    odc = option_data_client()
    feed = options_quote_feed()

    try:
        resp = tc.get_option_contracts(
            GetOptionContractsRequest(
                underlying_symbols=[underlying.upper()],
                status=AssetStatus.ACTIVE,
                expiration_date_gte=today,
                expiration_date_lte=end,
                strike_price_gte=f"{lo:g}",
                strike_price_lte=f"{hi:g}",
                limit=3000,
            )
        )
    except Exception:
        return None

    contracts = resp.option_contracts or []
    future = [
        c
        for c in contracts
        if c.tradable
        and c.expiration_date >= today
        and c.underlying_symbol.upper() == underlying.upper()
        and (c.expiration_date - today).days >= min_dte
    ]
    if not future:
        return None

    nearest_exp = min(c.expiration_date for c in future)
    bucket = [c for c in future if c.expiration_date == nearest_exp]
    strikes = sorted({round(float(c.strike_price), 4) for c in bucket})
    if not strikes:
        return None

    atm_strike = min(strikes, key=lambda s: abs(float(s) - ref_price))
    idx = strikes.index(atm_strike)
    lo_i = max(0, idx - n_ring)
    hi_i = min(len(strikes), idx + n_ring + 1)
    ring = strikes[lo_i:hi_i]

    calls: dict[float, str] = {}
    puts: dict[float, str] = {}
    for c in bucket:
        k = round(float(c.strike_price), 4)
        if k not in ring:
            continue
        if c.type == ContractType.CALL and k not in calls:
            calls[k] = c.symbol
        elif c.type == ContractType.PUT and k not in puts:
            puts[k] = c.symbol

    syms: list[str] = []
    for k in ring:
        if k in calls:
            syms.append(calls[k])
        if k in puts:
            syms.append(puts[k])
    if not syms:
        return None

    chain_snaps: dict[str, Any] | None = None
    try:
        chain_snaps = odc.get_option_chain(
            OptionChainRequest(
                underlying_symbol=underlying.upper(),
                feed=feed,
                expiration_date=nearest_exp.isoformat(),
                strike_price_gte=float(min(ring)) - 1e-6,
                strike_price_lte=float(max(ring)) + 1e-6,
            )
        )
    except Exception:
        chain_snaps = None

    def _snap(sym: str) -> dict[str, Any] | None:
        if isinstance(chain_snaps, dict) and sym in chain_snaps:
            return _opt_snap_fields(chain_snaps[sym])
        try:
            raw = odc.get_option_snapshot(
                OptionSnapshotRequest(symbol_or_symbols=sym, feed=feed)
            )
        except Exception:
            return None
        if isinstance(raw, dict) and sym in raw:
            return _opt_snap_fields(raw[sym])
        return None

    strikes_payload: dict[str, Any] = {}
    for k in ring:
        leg: dict[str, Any] = {"strike": k}
        cs = calls.get(k)
        ps = puts.get(k)
        if cs:
            leg["call"] = _snap(cs)
        if ps:
            leg["put"] = _snap(ps)
        strikes_payload[str(k)] = leg

    atm_leg = strikes_payload.get(str(atm_strike), {})
    return {
        "expiry": nearest_exp.isoformat(),
        "atm_strike": atm_strike,
        "atm": {
            "strike": atm_strike,
            "call": atm_leg.get("call"),
            "put": atm_leg.get("put"),
        },
        "strikes_ring": strikes_payload,
    }
