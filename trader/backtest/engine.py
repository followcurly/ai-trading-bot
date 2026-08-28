"""Daily event-loop backtest: lump_sum / dca / buy_hold + skim + idle dividend cash (paper untouched)."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timezone
from typing import Any, Callable

import pandas as pd

from trader.backtest.cash import CashState
from trader.backtest.data import align_calendar, fetch_cash_dividends, fetch_daily_bars
from trader.backtest.metrics import compute_report
from trader.backtest.store import save_run
from trader.backtest.universes import resolve_universe, universe_label
from trader.config import RED_DAY_BUY_PCT, RED_DAY_MIN_NOTIONAL
from trader.funds import FundUniverse

MODES = ("lump_sum", "dca", "buy_hold")
DCA_CADENCES = ("weekly", "monthly")
UNIVERSES = ("balanced", "high_yield", "high_appreciation")


def _add_months(d: date, months: int) -> date:
    import calendar as _cal

    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    return date(y, m, min(d.day, _cal.monthrange(y, m)[1]))


@dataclass
class BacktestParams:
    start: date
    end: date
    cash: float = 100_000.0
    cost_bps: float = 5.0  # per fill
    fill_rule: str = "next_open"  # or "same_close" (optimistic)
    mode: str = "buy_hold"  # lump_sum | dca | buy_hold
    red_lookback: int = 1  # red if close[T] < close[T-N] (buy_hold only)
    min_consecutive_reds: int = 1  # require N consecutive red signals
    min_down_pct: float = 0.0  # require (close[T-lb]-close[T])/close[T-lb] >= this
    ma_filter: int | None = None  # e.g. 200 → only buy if SPY > SMA(N)
    dca_cadence: str = "monthly"  # weekly | monthly
    dca_months: int = 6  # deploy over first N months then hold (0 = whole range)
    universe: str = "balanced"  # balanced | high_yield | high_appreciation
    year_end_skim: bool = False  # optional add-on for any mode
    skim_pct: float = 0.10  # when year_end_skim: sell this fraction of each holding at year-end
    buy_pct: float | None = None
    min_notional: float | None = None
    oos_frac: float = 0.30
    use_cache: bool = True

    def validate(self) -> None:
        if self.end < self.start:
            raise ValueError("end before start")
        if self.cash <= 0:
            raise ValueError("cash must be positive")
        if self.fill_rule not in ("next_open", "same_close"):
            raise ValueError("fill_rule must be next_open or same_close")
        if self.cost_bps < 0:
            raise ValueError("cost_bps must be >= 0")
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        if int(self.red_lookback) not in (1, 2, 3):
            raise ValueError("red_lookback must be 1, 2, or 3")
        if int(self.min_consecutive_reds) not in (1, 2, 3):
            raise ValueError("min_consecutive_reds must be 1, 2, or 3")
        if float(self.min_down_pct) < 0:
            raise ValueError("min_down_pct must be >= 0")
        if self.ma_filter is not None and int(self.ma_filter) not in (50, 100, 200):
            raise ValueError("ma_filter must be None, 50, 100, or 200")
        if self.dca_cadence not in DCA_CADENCES:
            raise ValueError("dca_cadence must be weekly or monthly")
        if int(self.dca_months) < 0 or int(self.dca_months) > 120:
            raise ValueError("dca_months must be 0..120")
        if self.year_end_skim and not (0.0 < float(self.skim_pct) <= 0.5):
            raise ValueError("skim_pct must be in (0, 0.5] when year_end_skim is on")
        # validate universe resolves
        resolve_universe(self.universe)



def _normalize_params(params: BacktestParams) -> BacktestParams:
    """Map legacy mode=year_end_skim → buy_hold + year_end_skim flag."""
    if params.mode == "year_end_skim":
        return replace(params, mode="buy_hold", year_end_skim=True)
    return params


def _is_red(bars: pd.DataFrame, dt: pd.Timestamp, lookback: int = 1) -> bool:
    lb = int(lookback)
    loc = bars.index.get_loc(dt)
    if isinstance(loc, slice) or not isinstance(loc, int):
        return False
    if loc < lb:
        return False
    return float(bars["close"].iloc[loc]) < float(bars["close"].iloc[loc - lb])


def _down_pct(bars: pd.DataFrame, dt: pd.Timestamp, lookback: int = 1) -> float:
    lb = int(lookback)
    loc = bars.index.get_loc(dt)
    if isinstance(loc, slice) or not isinstance(loc, int) or loc < lb:
        return 0.0
    prev = float(bars["close"].iloc[loc - lb])
    cur = float(bars["close"].iloc[loc])
    if prev <= 0:
        return 0.0
    return (prev - cur) / prev


def _consecutive_down_days(bars: pd.DataFrame, dt: pd.Timestamp) -> int:
    """Count consecutive sessions ending at dt with close[t] < close[t-1]."""
    loc = bars.index.get_loc(dt)
    if isinstance(loc, slice) or not isinstance(loc, int):
        return 0
    n = 0
    i = loc
    while i >= 1:
        if float(bars["close"].iloc[i]) < float(bars["close"].iloc[i - 1]):
            n += 1
            i -= 1
        else:
            break
    return n


def _px(bars: pd.DataFrame, dt: pd.Timestamp, field: str) -> float:
    return float(bars.at[dt, field])


def _sma(bars: pd.DataFrame, dt: pd.Timestamp, window: int) -> float | None:
    loc = bars.index.get_loc(dt)
    if isinstance(loc, slice) or not isinstance(loc, int):
        return None
    if loc + 1 < window:
        return None
    return float(bars["close"].iloc[loc - window + 1 : loc + 1].mean())


def _ma_ok(spy_bars: pd.DataFrame, dt: pd.Timestamp, ma_filter: int | None) -> bool:
    if ma_filter is None:
        return True
    sma = _sma(spy_bars, dt, int(ma_filter))
    if sma is None:
        return False
    return _px(spy_bars, dt, "close") > sma


def _signal_ok(
    bars: pd.DataFrame,
    dt: pd.Timestamp,
    *,
    lookback: int,
    min_consecutive_reds: int,
    min_down_pct: float,
) -> bool:
    if not _is_red(bars, dt, lookback):
        return False
    if _consecutive_down_days(bars, dt) < int(min_consecutive_reds):
        return False
    if _down_pct(bars, dt, lookback) + 1e-12 < float(min_down_pct):
        return False
    return True


def _is_dca_day(dt: pd.Timestamp, cadence: str, prev: pd.Timestamp | None) -> bool:
    if prev is None:
        return True
    if cadence == "weekly":
        # first trading day of each ISO week
        return dt.isocalendar().week != prev.isocalendar().week or dt.year != prev.year
    # monthly: first trading day of calendar month
    return dt.month != prev.month or dt.year != prev.year




def _dividend_events_by_date(
    symbols: list[str],
    start: date,
    end: date,
) -> dict[str, list[dict[str, Any]]]:
    """Map YYYY-MM-DD → cash dividend events for those symbols."""
    try:
        rows = fetch_cash_dividends(symbols, start, end)
    except Exception:
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for ev in rows:
        ex = str(ev.get("ex_date") or "")[:10]
        if not ex:
            continue
        out.setdefault(ex, []).append(ev)
    return out


def _apply_cash_dividends(
    *,
    day_key: str,
    events_by_date: dict[str, list[dict[str, Any]]],
    holdings: dict[str, float],
    cash: CashState,
    trades: list[dict[str, Any]],
) -> float:
    """Credit idle dividend cash for holdings entitled on ex-date. Returns $ credited today."""
    events = events_by_date.get(day_key) or []
    credited = 0.0
    for ev in events:
        sym = str(ev.get("symbol") or "").upper()
        qty = float(holdings.get(sym) or 0.0)
        if qty <= 1e-12:
            continue
        rate = float(ev.get("rate") or 0.0)
        if rate <= 0:
            continue
        amount = qty * rate
        cash.div += amount
        credited += amount
        trades.append(
            {
                "dt": day_key,
                "sleeve": None,
                "symbol": sym,
                "side": "div",
                "qty": qty,
                "price": rate,
                "notional": amount,
                "cost": 0.0,
                "end_value": None,
                "meta": {"note": "cash_dividend", "rate": rate},
            }
        )
    return credited


def _equity_row(
    *,
    day_key: str,
    mtm: float,
    spy_eq: float,
    cash: CashState,
) -> dict[str, Any]:
    snap = cash.snapshot()
    return {
        "dt": day_key,
        "strategy_equity": mtm,
        "spy_equity": spy_eq,
        "drawdown": 0.0,
        **snap,
        "invested": float(mtm) - float(snap["cash"]),
    }

def _queue_or_fill(
    *,
    order: dict[str, Any],
    dt: pd.Timestamp,
    day_key: str,
    params: BacktestParams,
    bars: dict[str, pd.DataFrame],
    pending: list[dict[str, Any]],
    holdings: dict[str, float],
    trades: list[dict[str, Any]],
    cash: CashState,
    fee_frac: float,
    min_notional: float,
) -> CashState:
    sym = order["symbol"]
    if params.fill_rule == "same_close":
        if sym in bars and dt in bars[sym].index:
            px = _px(bars[sym], dt, "close")
            holder = cash.as_holder()
            if order.get("side") == "sell":
                _execute_sell(order, px, fee_frac, holdings, trades, holder, fill_dt=day_key)
            else:
                _execute_buy(
                    order, px, fee_frac, holdings, trades, holder, min_notional, fill_dt=day_key
                )
            return cash
        return cash
    pending.append(order)
    return cash


def run_backtest(
    params: BacktestParams,
    *,
    persist: bool = True,
    progress: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Run a backtest. Never submits orders or writes trades.db."""
    params = _normalize_params(params)
    params.validate()
    uni = resolve_universe(params.universe)
    tickers = list(uni.all_tickers())
    if "SPY" not in tickers:
        tickers.append("SPY")

    pad = 14
    if params.ma_filter:
        pad = max(pad, int(params.ma_filter) * 2 + 30)

    def _bar_progress(sym: str, kind: str) -> None:
        if progress:
            progress("bars", {"symbol": sym, "kind": kind})

    bars = fetch_daily_bars(
        tickers,
        params.start,
        params.end,
        use_cache=params.use_cache,
        progress=_bar_progress,
        history_pad_days=pad,
        adjustment="split",  # idle dividends credited to cash_div (no double-count)
    )
    calendar = align_calendar(bars, params.start, params.end)
    if progress:
        progress("sim", {"days": int(len(calendar)), "mode": params.mode})

    buy_pct = RED_DAY_BUY_PCT if params.buy_pct is None else float(params.buy_pct)
    min_notional = (
        RED_DAY_MIN_NOTIONAL if params.min_notional is None else float(params.min_notional)
    )
    fee_frac = float(params.cost_bps) / 10_000.0

    if params.mode == "lump_sum":
        result = _run_lump_sum(
            params,
            uni=uni,
            bars=bars,
            calendar=calendar,
            fee_frac=fee_frac,
            min_notional=min_notional,
            buy_pct=buy_pct,
        )
    elif params.mode == "dca":
        result = _run_dca(
            params,
            uni=uni,
            bars=bars,
            calendar=calendar,
            fee_frac=fee_frac,
            min_notional=min_notional,
            buy_pct=buy_pct,
        )
    else:
        result = _run_buy_hold(
            params,
            uni=uni,
            bars=bars,
            calendar=calendar,
            fee_frac=fee_frac,
            min_notional=min_notional,
            buy_pct=buy_pct,
        )

    # When skim is on: also simulate the same plan with no skim, for chart/metrics.
    if params.year_end_skim:
        if progress:
            progress("skim_twin", {"note": "running no-skim twin for comparison"})
        twin = run_backtest(
            replace(params, year_end_skim=False),
            persist=False,
            progress=None,
        )
        result.setdefault("metrics", {})["skim"] = _skim_compare_metrics(result, twin)

    if persist:
        save_run(result)
    if progress:
        progress("done", {"run_id": result["id"], "trades": len(result.get("trades") or [])})
    return result


def _is_skim_trade(t: dict[str, Any]) -> bool:
    note = str((t.get("meta") or {}).get("note") or t.get("meta_note") or "")
    return t.get("side") == "sell" and "year_end_skim" in note


def _skim_compare_metrics(skim_run: dict[str, Any], no_skim_run: dict[str, Any]) -> dict[str, Any]:
    """Cash raised by skim + wealth vs identical plan that never skimmed."""
    trades = skim_run.get("trades") or []
    skim_trades = [t for t in trades if _is_skim_trade(t)]
    events: dict[str, float] = {}
    for t in skim_trades:
        dt = str(t.get("dt") or "")[:10]
        events[dt] = events.get(dt, 0.0) + float(t.get("notional") or 0.0)
    event_list = [{"dt": dt, "proceeds": events[dt]} for dt in sorted(events)]
    total_proceeds = sum(events.values())

    eq = skim_run.get("equity") or []
    end = eq[-1] if eq else {}
    end_eq = float(end.get("strategy_equity") or 0.0)
    end_cash = float(end.get("cash") or 0.0) if end.get("cash") is not None else None
    end_invested = (end_eq - end_cash) if end_cash is not None else None
    end_cash_skim = float(end["cash_skim"]) if end.get("cash_skim") is not None else None
    end_cash_div = float(end["cash_div"]) if end.get("cash_div") is not None else None
    end_cash_free = float(end["cash_free"]) if end.get("cash_free") is not None else None

    no_eq = no_skim_run.get("equity") or []
    no_end = float(no_eq[-1]["strategy_equity"]) if no_eq else None
    skim_ret = (skim_run.get("metrics") or {}).get("full") or {}
    no_ret = (no_skim_run.get("metrics") or {}).get("full") or {}
    start_cash = float(skim_run.get("cash") or 0.0)
    skim_total_ret = skim_ret.get("total_return")
    no_total_ret = no_ret.get("total_return")
    wealth_delta = (end_eq - no_end) if (no_end is not None) else None

    return {
        "enabled": True,
        "skim_pct": float((skim_run.get("params") or {}).get("skim_pct") or skim_run.get("skim_pct") or 0.1),
        "event_count": len(event_list),
        "events": event_list,
        "total_proceeds": total_proceeds,
        "ending_cash": end_cash,
        "ending_cash_skim": end_cash_skim,
        "ending_cash_div": end_cash_div,
        "ending_cash_free": end_cash_free,
        "ending_invested": end_invested,
        "ending_equity": end_eq,
        "no_skim_ending_equity": no_end,
        "no_skim_total_return": no_total_ret,
        "skim_total_return": skim_total_ret,
        "wealth_delta_vs_no_skim": wealth_delta,
        "wealth_delta_pct_pts": (
            None
            if skim_total_ret is None or no_total_ret is None
            else float(skim_total_ret) - float(no_total_ret)
        ),
        "starting_cash": start_cash,
        # Compact twin series for overlay (dates + equity only)
        "no_skim_equity": [
            {"dt": str(p["dt"])[:10], "strategy_equity": float(p["strategy_equity"])}
            for p in no_eq
        ],
        "note": (
            "Total equity = invested + cash_free + cash_skim + cash_div. "
            "Skim cash and dividend cash sit idle (not redeployed). "
            "No-skim line is the same plan with year-end skim off."
        ),
    }


def _finalize(
    params: BacktestParams,
    *,
    uni: FundUniverse,
    bars: dict[str, pd.DataFrame],
    calendar: pd.DatetimeIndex,
    equity_rows: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    buy_pct: float,
    min_notional: float,
    strategy_name: str,
) -> dict[str, Any]:
    last_dt = calendar[-1]
    for t in trades:
        sym = t["symbol"]
        qty = float(t["qty"])
        if t.get("side") == "sell":
            t["end_value"] = 0.0
            continue
        if t.get("side") == "div":
            t["end_value"] = None
            continue
        if sym in bars and last_dt in bars[sym].index:
            # remaining lot value approximation: not tracked per-lot after sells;
            # mark buys still held via current holdings is done in modes; here use price*qty
            # only if no later sell of same symbol after this buy — approximate end mark
            later_sell = any(
                x["symbol"] == sym and x.get("side") == "sell" and x["dt"] >= t["dt"]
                for x in trades
                if x.get("side") in ("buy", "sell")
            )
            if later_sell:
                t["end_value"] = None
            else:
                t["end_value"] = qty * _px(bars[sym], last_dt, "close")
        else:
            t["end_value"] = None

    strat = pd.Series(
        {pd.Timestamp(r["dt"]): r["strategy_equity"] for r in equity_rows}
    ).sort_index()
    spy_s = pd.Series({pd.Timestamp(r["dt"]): r["spy_equity"] for r in equity_rows}).sort_index()
    peak = strat.cummax()
    dd = (strat - peak) / peak.replace(0, pd.NA)
    for r in equity_rows:
        ts = pd.Timestamp(r["dt"])
        r["drawdown"] = float(dd.get(ts, 0.0) or 0.0)
        if r.get("cash") is not None:
            r["invested"] = float(r["strategy_equity"]) - float(r["cash"])

    # time invested: fraction of days with any holdings equity > cash-only roughly
    invested_days = sum(1 for r in equity_rows if r["strategy_equity"] > params.cash * 0.01)
    # better: track cash share — recompute from rows if we stored cash; use proxy via trades
    metrics = compute_report(strat, spy_s, trades, oos_frac=params.oos_frac)
    metrics["time_in_market_proxy"] = invested_days / max(len(equity_rows), 1)
    metrics["dividends"] = _estimate_dividends(
        trades,
        symbols=list(uni.all_tickers()) + ["SPY"],
        start=params.start,
        end=params.end,
        starting_cash=float(params.cash),
        ending_equity=float(strat.iloc[-1]) if len(strat) else float(params.cash),
    )
    # Prefer ledger totals from the sim (dividends credited to idle cash_div).
    if equity_rows:
        last = equity_rows[-1]
        div_end = float(last.get("cash_div") or 0.0)
        skim_end = float(last.get("cash_skim") or 0.0)
        free_end = float(last.get("cash_free") or 0.0)
        metrics["dividends"]["applied_to_cash"] = True
        metrics["dividends"]["reinvested"] = False
        metrics["dividends"]["ending_cash_div"] = div_end
        metrics["dividends"]["included_in_equity_curve"] = True
        metrics["dividends"]["note"] = (
            "Cash dividends credited to idle cash_div on each ex-date (not reinvested). "
            "Prices are split-adjusted only so yield is not double-counted in the curve."
        )
        # Keep estimate total aligned with ledger when present
        if div_end > 0:
            metrics["dividends"]["total_cash"] = div_end
        metrics["cash_breakdown"] = {
            "ending_free": free_end,
            "ending_skim": skim_end,
            "ending_div": div_end,
            "ending_total": free_end + skim_end + div_end,
        }

    run_id = uuid.uuid4().hex[:12]
    return {
        "id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "start_date": params.start.isoformat(),
        "end_date": params.end.isoformat(),
        "cash": float(params.cash),
        "cost_bps": float(params.cost_bps),
        "fill_rule": params.fill_rule,
        "red_lookback": int(params.red_lookback),
        "mode": params.mode,
        "year_end_skim": bool(params.year_end_skim),
        "skim_pct": float(params.skim_pct),
        "universe": params.universe,
        "status": "ok",
        "error": None,
        "metrics": metrics,
        "params": {
            **asdict(params),
            "start": params.start.isoformat(),
            "end": params.end.isoformat(),
            "red_lookback": int(params.red_lookback),
            "ma_filter": params.ma_filter,
            "dca_months": int(params.dca_months),
            "universe": params.universe,
            "universe_label": universe_label(params.universe),
            "buy_pct": buy_pct,
            "min_notional": min_notional,
            "strategy": strategy_name,
            "sleeves": [
                {
                    "name": s.name,
                    "target_pct": s.target_pct,
                    "primary": s.primary,
                    "tickers": list(s.tickers),
                }
                for s in uni.sleeves
            ],
        },
        "equity": equity_rows,
        "trades": trades,
    }


def _spy_benchmark(
    spy_bars: pd.DataFrame,
    calendar: pd.DatetimeIndex,
    cash0: float,
    fee_frac: float,
) -> tuple[float, float]:
    spy_cash = float(cash0)
    spy_shares = 0.0
    first = calendar[0]
    spy_open0 = _px(spy_bars, first, "open")
    if spy_open0 > 0:
        spend = spy_cash
        cost = spend * fee_frac
        invest = spend - cost
        spy_shares = invest / spy_open0
        spy_cash = 0.0
    return spy_cash, spy_shares


def _mtm(
    cash: float,
    holdings: dict[str, float],
    bars: dict[str, pd.DataFrame],
    uni: FundUniverse,
    dt: pd.Timestamp,
) -> tuple[float, dict[str, float]]:
    mtm = cash
    sleeve_mv: dict[str, float] = {s.name: 0.0 for s in uni.sleeves}
    for sym, qty in holdings.items():
        if qty <= 0 or sym not in bars or dt not in bars[sym].index:
            continue
        mv = qty * _px(bars[sym], dt, "close")
        mtm += mv
        sleeve = uni.sleeve_for(sym)
        if sleeve:
            sleeve_mv[sleeve.name] = sleeve_mv.get(sleeve.name, 0.0) + mv
    return mtm, sleeve_mv


def _apply_pending_opens(
    pending: list[dict[str, Any]],
    *,
    dt: pd.Timestamp,
    day_key: str,
    bars: dict[str, pd.DataFrame],
    holdings: dict[str, float],
    trades: list[dict[str, Any]],
    cash: CashState,
    fee_frac: float,
    min_notional: float,
) -> tuple[CashState, list[dict[str, Any]]]:
    still: list[dict[str, Any]] = []
    for order in pending:
        sym = order["symbol"]
        if sym not in bars or dt not in bars[sym].index:
            still.append(order)
            continue
        px = _px(bars[sym], dt, "open")
        holder = cash.as_holder()
        if order.get("side") == "sell":
            _execute_sell(order, px, fee_frac, holdings, trades, holder, fill_dt=day_key)
        else:
            _execute_buy(
                order, px, fee_frac, holdings, trades, holder, min_notional, fill_dt=day_key
            )
    return cash, still


def _is_last_session_of_year(calendar: pd.DatetimeIndex, i: int) -> bool:
    """True on the last trading day of a calendar year within the backtest."""
    dt = calendar[i]
    if i + 1 >= len(calendar):
        return dt.month == 12
    return int(dt.year) != int(calendar[i + 1].year)

def _apply_year_end_skim(
    *,
    params: BacktestParams,
    calendar: pd.DatetimeIndex,
    i: int,
    dt: pd.Timestamp,
    day_key: str,
    uni: FundUniverse,
    bars: dict[str, pd.DataFrame],
    holdings: dict[str, float],
    trades: list[dict[str, Any]],
    cash: CashState,
    fee_frac: float,
    min_notional: float,
) -> CashState:
    """Sell skim_pct of each holding on the last session of each calendar year (idle skim cash)."""
    if not params.year_end_skim:
        return cash
    if not _is_last_session_of_year(calendar, i) or not holdings:
        return cash
    skim_pct = float(params.skim_pct)
    for sym, qty in list(holdings.items()):
        if qty <= 0 or sym not in bars or dt not in bars[sym].index:
            continue
        sell_qty = qty * skim_pct
        px = _px(bars[sym], dt, "close")
        if sell_qty * px < min_notional:
            continue
        sleeve = uni.sleeve_for(sym)
        order = {
            "sleeve": sleeve.name if sleeve else None,
            "symbol": sym,
            "side": "sell",
            "qty": sell_qty,
            "signal_dt": day_key,
            "meta_note": f"year_end_skim_{skim_pct:.0%}",
            "credit_bucket": "skim",
        }
        holder = cash.as_holder()
        _execute_sell(order, px, fee_frac, holdings, trades, holder, fill_dt=day_key)
        if trades:
            trades[-1].setdefault("meta", {})["note"] = f"year_end_skim_{skim_pct:.0%}"
            trades[-1]["meta"]["cash_bucket"] = "skim"
    return cash




def _run_buy_hold(
    params: BacktestParams,
    *,
    uni: FundUniverse,
    bars: dict[str, pd.DataFrame],
    calendar: pd.DatetimeIndex,
    fee_frac: float,
    min_notional: float,
    buy_pct: float,
) -> dict[str, Any]:
    cash = CashState(free=float(params.cash))
    holdings: dict[str, float] = {}
    pending: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    equity_rows: list[dict[str, Any]] = []
    spy_bars = bars["SPY"]
    spy_cash, spy_shares = _spy_benchmark(spy_bars, calendar, params.cash, fee_frac)
    bought_sleeve_on_day: set[str] = set()
    prev_day: pd.Timestamp | None = None
    div_by_date = _dividend_events_by_date(
        list(uni.all_tickers()) + ["SPY"], params.start, params.end
    )

    for i, dt in enumerate(calendar):
        day_key = dt.strftime("%Y-%m-%d")
        if prev_day is None or day_key != prev_day.strftime("%Y-%m-%d"):
            bought_sleeve_on_day.clear()

        if params.fill_rule == "next_open" and pending:
            cash, pending = _apply_pending_opens(
                pending,
                dt=dt,
                day_key=day_key,
                bars=bars,
                holdings=holdings,
                trades=trades,
                cash=cash,
                fee_frac=fee_frac,
                min_notional=min_notional,
            )

        _apply_cash_dividends(
            day_key=day_key,
            events_by_date=div_by_date,
            holdings=holdings,
            cash=cash,
            trades=trades,
        )

        cash = _apply_year_end_skim(
            params=params,
            calendar=calendar,
            i=i,
            dt=dt,
            day_key=day_key,
            uni=uni,
            bars=bars,
            holdings=holdings,
            trades=trades,
            cash=cash,
            fee_frac=fee_frac,
            min_notional=min_notional,
        )

        mtm, sleeve_mv = _mtm(cash.total, holdings, bars, uni, dt)
        spy_eq = spy_cash + spy_shares * _px(spy_bars, dt, "close")
        equity_rows.append(
            _equity_row(day_key=day_key, mtm=mtm, spy_eq=spy_eq, cash=cash)
        )

        if mtm <= 0 or not _ma_ok(spy_bars, dt, params.ma_filter):
            prev_day = dt
            continue

        weights = []
        for sleeve in uni.sleeves:
            mv = sleeve_mv.get(sleeve.name, 0.0)
            w = mv / mtm if mtm > 0 else 0.0
            gap = sleeve.target_pct - w
            weights.append((sleeve, gap, gap > 1e-6))

        for sleeve, gap, under in sorted(weights, key=lambda x: x[1], reverse=True):
            if not under or sleeve.name in bought_sleeve_on_day:
                continue
            order_syms = (sleeve.primary,) + tuple(
                t for t in sleeve.tickers if t != sleeve.primary
            )
            pick = None
            for sym in order_syms:
                if sym not in bars or dt not in bars[sym].index:
                    continue
                if _signal_ok(
                    bars[sym],
                    dt,
                    lookback=params.red_lookback,
                    min_consecutive_reds=params.min_consecutive_reds,
                    min_down_pct=params.min_down_pct,
                ):
                    pick = sym
                    break
            if pick is None:
                continue
            # Only deploy free cash — skim + dividend cash stay idle
            notional = min(cash.free, mtm * buy_pct)
            if notional < min_notional:
                continue
            order = {
                "sleeve": sleeve.name,
                "symbol": pick,
                "side": "buy",
                "target_notional": notional,
                "signal_dt": day_key,
            }
            bought_sleeve_on_day.add(sleeve.name)
            cash = _queue_or_fill(
                order=order,
                dt=dt,
                day_key=day_key,
                params=params,
                bars=bars,
                pending=pending,
                holdings=holdings,
                trades=trades,
                cash=cash,
                fee_frac=fee_frac,
                min_notional=min_notional,
            )
        prev_day = dt

    return _finalize(
        params,
        uni=uni,
        bars=bars,
        calendar=calendar,
        equity_rows=equity_rows,
        trades=trades,
        buy_pct=buy_pct,
        min_notional=min_notional,
        strategy_name=("red_day_buy_hold_skim" if params.year_end_skim else "red_day_buy_hold"),
    )


def _estimate_dividends(
    trades: list[dict[str, Any]],
    *,
    symbols: list[str],
    start: date,
    end: date,
    starting_cash: float,
    ending_equity: float,
) -> dict[str, Any]:
    """Estimate cash dividends from holdings timeline × Alpaca cash dividend events."""
    try:
        events = fetch_cash_dividends(symbols, start, end)
    except Exception as e:
        return {
            "total_cash": 0.0,
            "by_symbol": {},
            "events": [],
            "share_of_gain": None,
            "note": f"dividend fetch failed: {e}",
            "included_in_equity_curve": True,
        }

    # Rebuild qty by day from fills
    fills = sorted(
        [x for x in trades if x.get("side") in ("buy", "sell")],
        key=lambda t: (t["dt"], 0 if t.get("side") == "buy" else 1),
    )
    holdings: dict[str, float] = {}
    # snapshot holdings at each ex_date
    by_sym: dict[str, float] = {}
    detailed: list[dict[str, Any]] = []
    total = 0.0

    # Walk calendar of event dates; apply trades up to and including prior day / ex date
    # Eligible if held at open of ex_date ≈ held after fills with dt < ex_date,
    # or dt == ex_date for buys before ex (conservative: require dt < ex_date)
    fi = 0
    for ev in events:
        ex = ev["ex_date"]
        while fi < len(fills) and fills[fi]["dt"] < ex:
            t = fills[fi]
            fi += 1
            if t.get("side") == "div":
                continue
            sym = t["symbol"]
            qty = float(t["qty"])
            if t.get("side") == "sell":
                holdings[sym] = max(0.0, holdings.get(sym, 0.0) - qty)
            elif t.get("side") == "buy":
                holdings[sym] = holdings.get(sym, 0.0) + qty
        qty = holdings.get(ev["symbol"], 0.0)
        if qty <= 1e-12:
            continue
        amount = qty * float(ev["rate"])
        total += amount
        by_sym[ev["symbol"]] = by_sym.get(ev["symbol"], 0.0) + amount
        detailed.append(
            {
                "ex_date": ex,
                "symbol": ev["symbol"],
                "qty": qty,
                "rate": float(ev["rate"]),
                "amount": amount,
            }
        )

    gain = ending_equity - starting_cash
    share = (total / gain) if gain > 1e-6 else None
    return {
        "total_cash": total,
        "by_symbol": by_sym,
        "events": detailed,
        "share_of_gain": share,
        "note": (
            "Equity curve uses dividend-adjusted prices (total return). "
            "Dollar amounts estimate cash dividends if shares were held on each ex-date."
        ),
        "included_in_equity_curve": True,
    }


def _buy_sleeve_primaries(
    *,
    budget: float,
    uni: FundUniverse,
    bars: dict[str, pd.DataFrame],
    dt: pd.Timestamp,
    day_key: str,
    params: BacktestParams,
    pending: list[dict[str, Any]],
    holdings: dict[str, float],
    trades: list[dict[str, Any]],
    cash: CashState,
    fee_frac: float,
    min_notional: float,
    meta_note: str,
) -> CashState:
    if budget < min_notional:
        return cash
    for sleeve in uni.sleeves:
        sym = sleeve.primary
        if sym not in bars or dt not in bars[sym].index:
            continue
        part = budget * float(sleeve.target_pct)
        if part < min_notional:
            continue
        order = {
            "sleeve": sleeve.name,
            "symbol": sym,
            "side": "buy",
            "target_notional": part,
            "signal_dt": day_key,
            "meta_note": meta_note,
        }
        cash = _queue_or_fill(
            order=order,
            dt=dt,
            day_key=day_key,
            params=params,
            bars=bars,
            pending=pending,
            holdings=holdings,
            trades=trades,
            cash=cash,
            fee_frac=fee_frac,
            min_notional=min_notional,
        )
    return cash


def _run_lump_sum(
    params: BacktestParams,
    *,
    uni: FundUniverse,
    bars: dict[str, pd.DataFrame],
    calendar: pd.DatetimeIndex,
    fee_frac: float,
    min_notional: float,
    buy_pct: float,
) -> dict[str, Any]:
    """Deploy free cash on day 1 into sleeve primaries; skim/div cash sit idle."""
    cash = CashState(free=float(params.cash))
    holdings: dict[str, float] = {}
    pending: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    equity_rows: list[dict[str, Any]] = []
    spy_bars = bars["SPY"]
    spy_cash, spy_shares = _spy_benchmark(spy_bars, calendar, params.cash, fee_frac)
    deployed = False
    div_by_date = _dividend_events_by_date(
        list(uni.all_tickers()) + ["SPY"], params.start, params.end
    )

    for i, dt in enumerate(calendar):
        day_key = dt.strftime("%Y-%m-%d")

        if params.fill_rule == "next_open" and pending:
            cash, pending = _apply_pending_opens(
                pending,
                dt=dt,
                day_key=day_key,
                bars=bars,
                holdings=holdings,
                trades=trades,
                cash=cash,
                fee_frac=fee_frac,
                min_notional=min_notional,
            )

        if not deployed:
            budget = cash.free
            for sleeve in uni.sleeves:
                sym = sleeve.primary
                if sym not in bars or dt not in bars[sym].index:
                    continue
                part = budget * float(sleeve.target_pct)
                if part < min_notional:
                    continue
                order = {
                    "sleeve": sleeve.name,
                    "symbol": sym,
                    "side": "buy",
                    "target_notional": part,
                    "signal_dt": day_key,
                    "meta_note": "lump_sum",
                }
                px = _px(bars[sym], dt, "open")
                holder = cash.as_holder()
                _execute_buy(
                    order, px, fee_frac, holdings, trades, holder, min_notional, fill_dt=day_key
                )
            deployed = True

        _apply_cash_dividends(
            day_key=day_key,
            events_by_date=div_by_date,
            holdings=holdings,
            cash=cash,
            trades=trades,
        )

        cash = _apply_year_end_skim(
            params=params,
            calendar=calendar,
            i=i,
            dt=dt,
            day_key=day_key,
            uni=uni,
            bars=bars,
            holdings=holdings,
            trades=trades,
            cash=cash,
            fee_frac=fee_frac,
            min_notional=min_notional,
        )

        mtm, _ = _mtm(cash.total, holdings, bars, uni, dt)
        spy_eq = spy_cash + spy_shares * _px(spy_bars, dt, "close")
        equity_rows.append(
            _equity_row(day_key=day_key, mtm=mtm, spy_eq=spy_eq, cash=cash)
        )

    return _finalize(
        params,
        uni=uni,
        bars=bars,
        calendar=calendar,
        equity_rows=equity_rows,
        trades=trades,
        buy_pct=buy_pct,
        min_notional=min_notional,
        strategy_name="lump_sum_skim" if params.year_end_skim else "lump_sum",
    )


def _run_dca(
    params: BacktestParams,
    *,
    uni: FundUniverse,
    bars: dict[str, pd.DataFrame],
    calendar: pd.DatetimeIndex,
    fee_frac: float,
    min_notional: float,
    buy_pct: float,
) -> dict[str, Any]:
    """DCA free cash over dca_months, then hold; skim/div cash sit idle."""
    cash = CashState(free=float(params.cash))
    holdings: dict[str, float] = {}
    pending: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    equity_rows: list[dict[str, Any]] = []
    spy_bars = bars["SPY"]
    spy_cash, spy_shares = _spy_benchmark(spy_bars, calendar, params.cash, fee_frac)
    div_by_date = _dividend_events_by_date(
        list(uni.all_tickers()) + ["SPY"], params.start, params.end
    )

    if int(params.dca_months) > 0:
        dca_until = pd.Timestamp(_add_months(params.start, int(params.dca_months)))
    else:
        dca_until = calendar[-1]

    dca_dates: list[pd.Timestamp] = []
    prev = None
    for dt in calendar:
        if dt > dca_until:
            break
        if _is_dca_day(dt, params.dca_cadence, prev):
            dca_dates.append(dt)
        prev = dt

    n = max(len(dca_dates), 1)
    slice_cash = float(params.cash) / n
    dca_set = {d.strftime("%Y-%m-%d") for d in dca_dates}
    slices_left = n

    for i, dt in enumerate(calendar):
        day_key = dt.strftime("%Y-%m-%d")

        if params.fill_rule == "next_open" and pending:
            cash, pending = _apply_pending_opens(
                pending,
                dt=dt,
                day_key=day_key,
                bars=bars,
                holdings=holdings,
                trades=trades,
                cash=cash,
                fee_frac=fee_frac,
                min_notional=min_notional,
            )

        _apply_cash_dividends(
            day_key=day_key,
            events_by_date=div_by_date,
            holdings=holdings,
            cash=cash,
            trades=trades,
        )

        cash = _apply_year_end_skim(
            params=params,
            calendar=calendar,
            i=i,
            dt=dt,
            day_key=day_key,
            uni=uni,
            bars=bars,
            holdings=holdings,
            trades=trades,
            cash=cash,
            fee_frac=fee_frac,
            min_notional=min_notional,
        )

        mtm, _ = _mtm(cash.total, holdings, bars, uni, dt)
        spy_eq = spy_cash + spy_shares * _px(spy_bars, dt, "close")
        equity_rows.append(
            _equity_row(day_key=day_key, mtm=mtm, spy_eq=spy_eq, cash=cash)
        )

        if day_key not in dca_set or slices_left <= 0:
            continue

        budget = min(cash.free, slice_cash)
        if budget < min_notional:
            continue

        cash = _buy_sleeve_primaries(
            budget=budget,
            uni=uni,
            bars=bars,
            dt=dt,
            day_key=day_key,
            params=params,
            pending=pending,
            holdings=holdings,
            trades=trades,
            cash=cash,
            fee_frac=fee_frac,
            min_notional=min_notional,
            meta_note=f"dca_{params.dca_cadence}_{params.dca_months}m",
        )
        slices_left -= 1

    return _finalize(
        params,
        uni=uni,
        bars=bars,
        calendar=calendar,
        equity_rows=equity_rows,
        trades=trades,
        buy_pct=buy_pct,
        min_notional=min_notional,
        strategy_name=(f"dca_{params.dca_cadence}_{params.dca_months}m_skim" if params.year_end_skim else f"dca_{params.dca_cadence}_{params.dca_months}m"),
    )


def _execute_buy(
    order: dict[str, Any],
    px: float,
    fee_frac: float,
    holdings: dict[str, float],
    trades: list[dict[str, Any]],
    cash_holder: dict[str, Any],
    min_notional: float,
    *,
    fill_dt: str,
) -> None:
    """Spend only deployable (free) cash — skim/div stay idle."""
    state: CashState = cash_holder["state"]
    target = min(float(state.free), float(order["target_notional"]))
    if target < min_notional or px <= 0:
        return
    cost = target * fee_frac
    invest = target - cost
    if invest <= 0:
        return
    qty = invest / px
    state.free -= target
    sym = order["symbol"]
    holdings[sym] = holdings.get(sym, 0.0) + qty
    meta = {"signal_dt": order.get("signal_dt"), "gross": target, "cash_bucket": "free"}
    if order.get("meta_note"):
        meta["note"] = order["meta_note"]
    trades.append(
        {
            "dt": fill_dt,
            "sleeve": order.get("sleeve"),
            "symbol": sym,
            "side": "buy",
            "qty": qty,
            "price": px,
            "notional": invest,
            "cost": cost,
            "end_value": None,
            "meta": meta,
        }
    )


def _execute_sell(
    order: dict[str, Any],
    px: float,
    fee_frac: float,
    holdings: dict[str, float],
    trades: list[dict[str, Any]],
    cash_holder: dict[str, Any],
    *,
    fill_dt: str,
) -> None:
    """Credit proceeds to skim bucket for year-end skim, else free."""
    state: CashState = cash_holder["state"]
    sym = order["symbol"]
    qty = float(order.get("qty") or holdings.get(sym, 0.0))
    qty = min(qty, holdings.get(sym, 0.0))
    if qty <= 0 or px <= 0:
        return
    gross = qty * px
    cost = gross * fee_frac
    proceeds = gross - cost
    holdings[sym] = holdings.get(sym, 0.0) - qty
    if holdings[sym] <= 1e-12:
        holdings.pop(sym, None)
    note = str(order.get("meta_note") or "")
    bucket = str(order.get("credit_bucket") or ("skim" if "year_end_skim" in note else "free"))
    if bucket == "skim":
        state.skim += proceeds
    elif bucket == "div":
        state.div += proceeds
    else:
        state.free += proceeds
    trades.append(
        {
            "dt": fill_dt,
            "sleeve": order.get("sleeve"),
            "symbol": sym,
            "side": "sell",
            "qty": qty,
            "price": px,
            "notional": proceeds,
            "cost": cost,
            "end_value": 0.0,
            "meta": {
                "signal_dt": order.get("signal_dt"),
                "gross": gross,
                "cash_bucket": bucket,
            },
        }
    )


def run_lookback_compare(
    params_base: BacktestParams,
    *,
    lookbacks: list[int] | tuple[int, ...] | None = None,
    progress: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Compare selected buy-the-dip lookbacks (mode=buy_hold; skim flag from params)."""
    params_base = _normalize_params(params_base)
    params_base.validate()
    lbs = list(lookbacks) if lookbacks else [1, 2, 3]
    lbs = sorted({int(x) for x in lbs if int(x) in (1, 2, 3)})
    if len(lbs) < 2:
        raise ValueError("Pick at least two lookbacks to compare")
    runs: list[dict[str, Any]] = []
    for lb in lbs:
        if progress:
            progress("lookback", {"red_lookback": lb})
        p = replace(params_base, mode="buy_hold", red_lookback=lb)
        runs.append(run_backtest(p, persist=True, progress=progress))
    return {
        "kind": "lookback",
        "runs": runs,
        "ids": [r["id"] for r in runs],
        "lookbacks": lbs,
    }


def run_strategy_compare(
    params_base: BacktestParams,
    *,
    modes: list[str] | tuple[str, ...] | None = None,
    progress: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Compare selected base plans; year_end_skim from params applies to every series."""
    params_base = _normalize_params(params_base)
    params_base.validate()
    allowed = ("lump_sum", "dca", "buy_hold")
    if modes is None:
        mode_list = ["lump_sum", "dca", "buy_hold"]
    else:
        mode_list = []
        seen: set[str] = set()
        for m in modes:
            if m == "year_end_skim":
                m = "buy_hold"  # legacy checkbox value
            if m in allowed and m not in seen:
                seen.add(m)
                mode_list.append(m)
    if len(mode_list) < 2:
        raise ValueError("Pick at least two plans to compare")
    runs: list[dict[str, Any]] = []
    for mode in mode_list:
        if progress:
            progress("strategy", {"mode": mode})
        p = replace(params_base, mode=mode)
        runs.append(run_backtest(p, persist=True, progress=progress))
    return {"kind": "strategy", "runs": runs, "ids": [r["id"] for r in runs], "modes": mode_list}


def run_universe_compare(
    params_base: BacktestParams,
    *,
    universes: list[str] | tuple[str, ...] | None = None,
    progress: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Compare selected ETF mixes with the same plan (skim flag from params)."""
    params_base = _normalize_params(params_base)
    params_base.validate()
    if universes is None:
        uni_list = list(UNIVERSES)
    else:
        seen: set[str] = set()
        uni_list = []
        for u in universes:
            u = str(u).strip().lower()
            if u in UNIVERSES and u not in seen:
                seen.add(u)
                uni_list.append(u)
    if len(uni_list) < 2:
        raise ValueError("Pick at least two ETF mixes to compare")
    runs: list[dict[str, Any]] = []
    for uni in uni_list:
        if progress:
            progress("universe", {"universe": uni})
        p = replace(params_base, universe=uni)
        runs.append(run_backtest(p, persist=True, progress=progress))
    return {
        "kind": "universe",
        "runs": runs,
        "ids": [r["id"] for r in runs],
        "universes": uni_list,
    }
