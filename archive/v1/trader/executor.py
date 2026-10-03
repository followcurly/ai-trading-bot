"""Alpaca order execution — long_stock brackets; single-leg long options (calls/puts)."""

from __future__ import annotations

import math
import os
import time

from alpaca.common.exceptions import APIError
from alpaca.trading.enums import OrderClass, OrderSide, PositionIntent, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import GetOrdersRequest, MarketOrderRequest

from trader.alpaca_runtime import option_data_client, trading_client
from trader.options_exec import (
    contracts_qty_for_buy,
    conviction_contract_cap,
    estimate_premium_per_share,
    fetch_option_bid_ask,
    fetch_tradable_contract,
    parse_expiry_date,
)

# Alpaca: bracket orders cannot use fractional share qty (42210000: fractional orders must be simple orders).

_ATR_MULT_STOP = float(os.getenv("ATR_MULTIPLIER_STOP", "1.0"))
_ATR_MULT_TP = float(os.getenv("ATR_MULTIPLIER_TP", "1.5"))
_OPTIONS_REQUIRE_BID = os.getenv("OPTIONS_REQUIRE_BID", "true").strip().lower() in (
    "1", "true", "yes", "on"
)


def _has_open_position(tc, sym: str) -> bool:
    try:
        tc.get_open_position(sym)
        return True
    except APIError:
        return False


def _cancel_open_sells_for(tc, sym: str) -> int:
    """Cancel all working SELL orders on `sym` (e.g. bracket take-profit/stop legs that
    reserve shares and block manual exits). Returns count successfully cancelled.

    Why: when the brain decides to exit early via market SELL, Alpaca will reject because
    the bracket children already reserve the shares (qty_available=0). We free them first.
    """
    try:
        orders = tc.get_orders(
            filter=GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[sym])
        )
    except Exception:
        return 0
    cancelled = 0
    for o in orders:
        side_val = getattr(getattr(o, "side", None), "value", None) or str(getattr(o, "side", ""))
        if "sell" not in side_val.lower():
            continue
        try:
            tc.cancel_order_by_id(o.id)
            cancelled += 1
        except Exception:
            continue
    return cancelled


def _submit_order(tc, req: MarketOrderRequest):
    try:
        return tc.submit_order(req)
    except APIError:
        time.sleep(1.0)
        return tc.submit_order(req)


def _option_close_qty(tc, option_symbol: str) -> int | None:
    try:
        p = tc.get_open_position(option_symbol)
    except APIError:
        return None
    raw = float(getattr(p, "qty_available", None) or p.qty or 0)
    q = int(abs(raw))
    return q if q >= 1 else None


def _clamp_bracket_prices(
    action: str,
    ref: float,
    stop: float,
    tp: float,
    atr: float,
) -> tuple[float, float] | None:
    """Return (stop, tp) adjusted to minimum ATR distance from ref, or None if any
    leg is non-positive / NaN / wrong-side of ref.

    Sub-dollar names where ``ATR > ref`` (e.g. EZGO at $0.05 with ATR $0.40) cannot
    produce a valid long bracket — ``ref - ATR`` goes negative — so we return
    ``None`` and let the caller skip with ``atr_clamp_infeasible_stop_tp``. The
    watchlist's ``WATCHLIST_ENFORCE_MIN_PRICE`` is the upstream guard; this is
    defense-in-depth at the broker boundary.
    """
    if ref <= 0 or atr <= 0 or math.isnan(atr) or math.isnan(ref):
        return None

    def _finite_positive(*xs: float) -> bool:
        return all(x > 0 and not math.isnan(x) for x in xs)

    min_stop = atr * _ATR_MULT_STOP
    min_tp = atr * _ATR_MULT_TP
    stop_f = float(stop)
    tp_f = float(tp)

    if action == "BUY":
        floor_stop = ref - min_stop
        ceil_tp = ref + min_tp
        # Model may send 0 — anchor to ATR floors so bracket legs are valid
        if stop_f <= 0 or stop_f > floor_stop:
            stop_f = floor_stop
        if tp_f <= 0 or tp_f < ceil_tp:
            tp_f = ceil_tp
        if not _finite_positive(stop_f, tp_f):
            return None
        if stop_f >= ref or tp_f <= ref:
            return None
        stop_r, tp_r = round(stop_f, 2), round(tp_f, 2)
        if not _finite_positive(stop_r, tp_r):
            return None
        if stop_r >= ref or tp_r <= ref:  # rounding can still cross
            return None
        return stop_r, tp_r

    if action == "SELL":
        # Bracket sell: stop above ref, take-profit below ref (Alpaca sell bracket convention)
        ceil_stop = ref + min_stop
        floor_tp = ref - min_tp
        if stop_f <= 0 or stop_f < ceil_stop:
            stop_f = ceil_stop
        if tp_f <= 0 or tp_f > floor_tp:
            tp_f = floor_tp
        if not _finite_positive(stop_f, tp_f):
            return None
        if stop_f <= ref or tp_f >= ref:
            return None
        stop_r, tp_r = round(stop_f, 2), round(tp_f, 2)
        if not _finite_positive(stop_r, tp_r):
            return None
        if stop_r <= ref or tp_r >= ref:
            return None
        return stop_r, tp_r

    return None


def _execute_single_leg_option(
    decision: dict,
    strategy: str,
    underlying: str,
    action: str,
) -> dict:
    exp = parse_expiry_date(str(decision.get("option_expiry") or ""))
    if exp is None:
        return {"status": "skipped", "reason": "invalid_or_missing_option_expiry"}
    try:
        strike = float(decision.get("option_strike"))
    except (TypeError, ValueError):
        return {"status": "skipped", "reason": "invalid_option_strike"}
    if strike <= 0 or math.isnan(strike):
        return {"status": "skipped", "reason": "invalid_option_strike"}

    tc = trading_client()
    contract = fetch_tradable_contract(tc, underlying, strategy, strike, exp)
    if contract is None:
        return {
            "status": "skipped",
            "reason": "option_contract_not_found_or_untradable",
        }
    osym = contract.symbol
    meta: dict = {"option_symbol": osym, "underlying": underlying}

    if action == "SELL":
        qty_close = _option_close_qty(tc, osym)
        if qty_close is None:
            return {"status": "skipped", "reason": "no_open_option_position", **meta}
        # Pre-check: Alpaca rejects market SELL with 40310000 when there is no
        # available bid quote. Skip cleanly instead of burning the round-trip and
        # cluttering the journal with retry-after-retry errors (see FNDX 2026-05-12).
        if _OPTIONS_REQUIRE_BID:
            bid, ask = fetch_option_bid_ask(option_data_client(), osym)
            if not (bid and bid > 0):
                return {
                    "status": "skipped",
                    "reason": "option_no_bid_for_market_sell",
                    "bid": bid,
                    "ask": ask,
                    **meta,
                }
        req = MarketOrderRequest(
            symbol=osym,
            qty=float(qty_close),
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
            position_intent=PositionIntent.SELL_TO_CLOSE,
        )
        try:
            order = _submit_order(tc, req)
        except APIError as e:
            return {"status": "error", "reason": str(e), **meta}
        return {
            "order_id": str(order.id),
            "status": str(order.status),
            "option_qty": qty_close,
            **meta,
        }

    # BUY — open long
    # Liquidity gate: require both a real bid and ask before opening a new option
    # position. Prevents getting trapped in dormant contracts (e.g. low-volume ETF
    # options) that have an offer but no buyer, leading to stuck positions that
    # can only exit at $0 / expiry. Disable with OPTIONS_REQUIRE_BID=false.
    if _OPTIONS_REQUIRE_BID:
        bid_pre, ask_pre = fetch_option_bid_ask(option_data_client(), osym)
        if not (bid_pre and bid_pre > 0 and ask_pre and ask_pre > 0):
            return {
                "status": "skipped",
                "reason": "option_no_bid_at_buy",
                "bid": bid_pre,
                "ask": ask_pre,
                **meta,
            }

    prem = estimate_premium_per_share(option_data_client(), osym, contract)
    if prem is None or prem <= 0 or math.isnan(prem):
        return {"status": "skipped", "reason": "no_option_quote_for_sizing", **meta}
    meta["estimated_premium_per_share"] = round(prem, 4)

    equity = float(tc.get_account().equity)
    size_pct = float(decision.get("size_pct", 0) or 0)
    try:
        conf = float(decision.get("confidence") or 0)
    except (TypeError, ValueError):
        conf = 0.0
    cap = conviction_contract_cap(conf)
    n = contracts_qty_for_buy(equity, size_pct, prem, cap)
    if n is None:
        return {"status": "skipped", "reason": "option_qty_lt_1_for_budget", **meta}
    meta["option_qty"] = n
    meta["conviction_contract_cap"] = cap

    req = MarketOrderRequest(
        symbol=osym,
        qty=float(n),
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
        position_intent=PositionIntent.BUY_TO_OPEN,
    )
    try:
        order = _submit_order(tc, req)
    except APIError as e:
        return {"status": "error", "reason": str(e), **meta}
    return {"order_id": str(order.id), "status": str(order.status), **meta}


def execute(
    decision: dict,
    reference_price: float,
    atr_14: float | None = None,
) -> dict:
    """Place orders. `reference_price` is live or last close from snapshot (not trusted from LLM)."""
    action = decision.get("action")
    if action not in ("BUY", "SELL"):
        return {"status": "skipped", "reason": f"action={action}"}

    strategy = decision.get("strategy")
    symbol = (decision.get("symbol") or "").upper()
    stop = float(decision.get("stop_loss") or 0)
    tp = float(decision.get("take_profit") or 0)

    if strategy == "long_stock" and reference_price > 0:
        tc = trading_client()

        # Close existing long: simple DAY market sell (Alpaca rejects bracket as non-entry).
        if action == "SELL":
            if not _has_open_position(tc, symbol):
                return {"status": "skipped", "reason": "no_open_position"}

            # Free shares reserved by working bracket TP/SL or other open SELL orders so
            # the market exit can claim them. No-op when no working sells exist.
            cancelled_open_sells = _cancel_open_sells_for(tc, symbol)
            if cancelled_open_sells:
                time.sleep(0.5)

            try:
                pos_alp = tc.get_open_position(symbol)
                qty = float(
                    int(
                        math.floor(
                            abs(
                                float(
                                    getattr(pos_alp, "qty_available", None)
                                    or pos_alp.qty
                                    or 0
                                )
                            )
                        )
                    )
                )
            except APIError:
                qty = 0.0
            if qty < 1:
                return {
                    "status": "skipped",
                    "reason": "no_whole_shares_to_sell",
                    "cancelled_open_sells": cancelled_open_sells,
                }
            meta = {"exit_type": "simple_market", "cancelled_open_sells": cancelled_open_sells}
            req = MarketOrderRequest(
                symbol=symbol,
                qty=qty,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.DAY,
            )
            try:
                order = _submit_order(tc, req)
            except APIError as e:
                return {"status": "error", "reason": str(e), **meta}
            return {"order_id": str(order.id), "status": str(order.status), **meta}

        # BUY: bracket entry (GTC) with ATR-clamped stop / take-profit
        atr = float(atr_14) if atr_14 is not None else 0.0
        if atr <= 0 or math.isnan(atr):
            return {"status": "skipped", "reason": "missing_or_invalid_atr_14"}

        clamped = _clamp_bracket_prices("BUY", reference_price, stop, tp, atr)
        if clamped is None:
            return {"status": "skipped", "reason": "atr_clamp_infeasible_stop_tp"}
        stop, tp = clamped

        equity = float(tc.get_account().equity)
        notional = float(decision.get("size_pct", 0)) * equity
        qty_raw = notional / reference_price
        qty = float(int(math.floor(qty_raw)))
        if qty < 1:
            return {
                "status": "skipped",
                "reason": "qty<1_whole_shares_bracket (raise size_pct or equity)",
            }

        meta = {"clamped_stop": stop, "clamped_take_profit": tp}
        req = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.GTC,
            order_class=OrderClass.BRACKET,
            stop_loss={"stop_price": stop},
            take_profit={"limit_price": tp},
        )
        try:
            order = _submit_order(tc, req)
        except APIError as e:
            return {"status": "error", "reason": str(e), **meta}
        return {"order_id": str(order.id), "status": str(order.status), **meta}

    if strategy in ("long_call", "long_put"):
        return _execute_single_leg_option(decision, strategy, symbol, action)

    return {"status": "skipped", "reason": "strategy_not_executable_in_executor"}
