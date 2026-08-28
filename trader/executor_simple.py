"""Simple market buy by notional (fraction of equity, cash-capped)."""

from __future__ import annotations

import logging
from typing import Any

from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.trading.requests import MarketOrderRequest

from trader.alpaca_runtime import trading_client
from trader.config import RED_DAY_BUY_PCT, RED_DAY_MIN_NOTIONAL

log = logging.getLogger("trader.executor_simple")


def market_buy_notional(
    symbol: str,
    *,
    equity: float,
    cash: float,
    buy_pct: float | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Submit a notional market BUY. Returns execute-result dict for journal."""
    sym = symbol.strip().upper()
    pct = RED_DAY_BUY_PCT if buy_pct is None else float(buy_pct)
    notional = min(float(cash), float(equity) * pct)
    if notional < RED_DAY_MIN_NOTIONAL:
        return {
            "status": "skipped",
            "reason": f"notional_too_small={notional:.2f}",
            "notional": notional,
            "symbol": sym,
        }

    # Alpaca notional market orders for stocks/ETFs
    req = MarketOrderRequest(
        symbol=sym,
        notional=round(notional, 2),
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
    )
    if dry_run:
        return {
            "status": "skipped",
            "reason": "dry_run",
            "notional": round(notional, 2),
            "symbol": sym,
        }

    try:
        order = trading_client().submit_order(req)
    except Exception as e:
        log.exception("market buy failed symbol=%s", sym)
        return {
            "status": "error",
            "reason": str(e),
            "notional": round(notional, 2),
            "symbol": sym,
        }

    oid = str(getattr(order, "id", "") or "")
    return {
        "status": "placed" if oid else "error",
        "order_id": oid or None,
        "reason": None if oid else "no_order_id",
        "notional": round(notional, 2),
        "symbol": sym,
        "side": "buy",
    }
