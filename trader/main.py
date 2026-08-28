"""APScheduler: 4× per US market day (10:00, 12:00, 14:00, 15:30 ET) — snapshot → Claude → risk → execute → journal."""

from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import time
from datetime import date as date_cls, datetime, time as time_of_day, timedelta, timezone
from pathlib import Path

import requests
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv

load_dotenv()

from trader.alpaca_runtime import trading_client
from trader.claude_brain import get_decision
from trader.config import (
    ALPACA_PAPER,
    DB_PATH,
    JSONL_PATH,
    OPTIONS_EXPIRY_SWEEP_DISABLE,
    OPTIONS_EXPIRY_SWEEP_TIME_ET,
    OPTIONS_PROFIT_LADDER_DISABLE,
    OPTIONS_PROFIT_LADDER_FRACTION,
    OPTIONS_PROFIT_LADDER_PCT,
    REPO_ROOT,
    TRADING_PROFILE,
    TRAIL_RESET_DAILY,
)
from trader.profile import check_live_profile_guard, effective_trail_min_track_pnl
from trader.data_feed import get_market_snapshot
from trader.db import connect as _db_connect
from trader.regime import get_market_regime
from trader.executor import execute
from trader.journal import build_logic_json, write_journal_entry
from trader.market_hours import ET, is_us_equity_rth
from trader.options_exec import parse_occ_us_option_symbol
from trader.risk_engine import (
    _ensure_risk_state,
    _risk_kv_get,
    _risk_kv_set,
    record_day_trade,
    validate,
)
from trader.watchlist import get_watchlist


def prewarm_caches() -> dict[str, int]:
    """Warm symbol-agnostic TTL caches in parallel at process start.

    Pays ~10-15s of cold-cache cost ONCE at boot rather than charging it to
    whichever symbol happens to be first in the next scan cycle (SPY was
    routinely taking 50s as a result). All fetches are best-effort; one slow
    source cannot block startup.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from trader.enrichment import _fetch_rss_bundle
    from trader.macro import enrich_macro
    from trader.sentiment import fetch_fear_greed
    from trader.finnhub import fetch_earnings_calendar

    tasks: dict[str, callable] = {
        "rss_bundle": _fetch_rss_bundle,
        "macro": lambda: enrich_macro({}),
        "fear_greed": fetch_fear_greed,
        "earnings_calendar": fetch_earnings_calendar,
    }
    timings: dict[str, int] = {}
    with ThreadPoolExecutor(max_workers=len(tasks)) as ex:
        futures = {ex.submit(_timed_call, name, fn): name for name, fn in tasks.items()}
        for f in as_completed(futures):
            name = futures[f]
            try:
                _, ms = f.result()
                timings[name] = ms
            except Exception as e:
                log.warning("prewarm_fail name=%s err=%s", name, e)
                timings[name] = -1
    log.info("prewarm_caches timings_ms=%s", json.dumps(timings))
    return timings


def _timed_call(name: str, fn) -> tuple[object, int]:
    t0 = time.perf_counter()
    try:
        return fn(), int((time.perf_counter() - t0) * 1000)
    except Exception:
        return None, int((time.perf_counter() - t0) * 1000)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("trader")

# yfinance logs "No earnings dates found, symbol may be delisted" at ERROR for every ETF /
# symbol that simply has no earnings entry. It is noise, not a failure — silence it.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

def ping_healthcheck(ok: bool = True) -> None:
    url = os.getenv("HEALTHCHECK_URL", "").strip()
    if not url:
        return
    try:
        if ok:
            requests.get(url, timeout=5)
        else:
            fail_u = os.getenv("HEALTHCHECK_FAIL_URL", "").strip()
            if fail_u:
                requests.get(fail_u, timeout=5)
            else:
                requests.get(url.rstrip("/") + "/fail", timeout=5)
    except OSError:
        pass


def _append_metrics_line(payload: dict) -> None:
    raw = (os.getenv("TRADING_METRICS_JSONL_PATH") or "").strip()
    if not raw:
        return
    path = Path(raw).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, default=str) + "\n")


def _ensure_trail_table(conn: sqlite3.Connection) -> None:
    """Create-or-migrate the trail/exit state table.

    Adds new columns idempotently for the four exit triggers:
    - peak_unrealized_pnl  -- multi-day high-water-mark for trail-giveback
    - exit_fired           -- trail-giveback already submitted
    - profit_ladder_rungs_fired  -- CSV of rung indices already harvested (e.g. "0,1")
    - expiry_sweep_fired   -- T-0 expiry sweep already submitted
    - initial_qty          -- qty observed when row was created (for ladder sizing)
    """
    conn.execute(
        """CREATE TABLE IF NOT EXISTS position_trail_state (
            symbol TEXT PRIMARY KEY,
            peak_unrealized_pnl REAL NOT NULL DEFAULT 0,
            asof_date_et TEXT NOT NULL,
            exit_fired INTEGER NOT NULL DEFAULT 0
        )"""
    )
    existing = {r[1] for r in conn.execute("PRAGMA table_info(position_trail_state)").fetchall()}
    migrations = [
        ("profit_ladder_rungs_fired", "TEXT NOT NULL DEFAULT ''"),
        ("expiry_sweep_fired", "INTEGER NOT NULL DEFAULT 0"),
        ("initial_qty", "REAL NOT NULL DEFAULT 0"),
    ]
    for col, ddl in migrations:
        if col not in existing:
            conn.execute(f"ALTER TABLE position_trail_state ADD COLUMN {col} {ddl}")
    conn.commit()


def _parse_et_clock(raw: str) -> time_of_day:
    """Parse 'HH:MM' as ET wall-clock; fall back to 15:00 on malformed input."""
    s = (raw or "").strip()
    try:
        h, m = s.split(":", 1)
        return time_of_day(max(0, min(23, int(h))), max(0, min(59, int(m))))
    except (ValueError, AttributeError):
        return time_of_day(15, 0)


def _csv_int_set(raw: str) -> set[int]:
    out: set[int] = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            continue
    return out


def _ladder_qty_to_close(
    rung_idx: int,
    initial_qty: int,
    fired_rungs: set[int],
    fractions: list[float],
    current_qty: int,
) -> int:
    """How many contracts to close at rung `rung_idx`.

    Cumulative semantics: fraction[i] is the fraction of *initial* qty closed by
    that rung. We round UP so a 1-contract position with a 50% rung still exits 1
    (otherwise floor(0.5 * 1) = 0 and the rung would be a no-op for small sizes).
    The result is capped at the current remaining qty.
    """
    if rung_idx in fired_rungs or initial_qty <= 0 or current_qty <= 0:
        return 0
    if rung_idx >= len(fractions):
        return 0
    target_closed = math.ceil(fractions[rung_idx] * initial_qty)
    already_closed = max(0, initial_qty - current_qty)
    qty = max(0, target_closed - already_closed)
    return min(qty, current_qty)


def _submit_option_partial_close(
    parsed_occ: dict,
    qty: int,
    rationale: str,
) -> dict:
    """Submit a partial market SELL on an OCC option contract.

    Falls through to the regular options executor for the simple/full-close case
    (qty == current open qty). For partial closes we submit directly because the
    executor's option-SELL path always closes the full open quantity.
    """
    from alpaca.trading.enums import OrderSide, PositionIntent, TimeInForce
    from alpaca.trading.requests import MarketOrderRequest

    from trader.alpaca_runtime import option_data_client, trading_client as _tc
    from trader.options_exec import fetch_option_bid_ask

    underlying = parsed_occ["underlying"]
    strategy = parsed_occ["strategy"]
    osym = None
    tc = _tc()
    try:
        from trader.options_exec import fetch_tradable_contract

        contract = fetch_tradable_contract(
            tc,
            underlying,
            strategy,
            float(parsed_occ["strike"]),
            parsed_occ["expiry"],
        )
        if contract is None:
            return {
                "status": "skipped",
                "reason": "option_contract_not_found_or_untradable",
                "underlying": underlying,
            }
        osym = contract.symbol
    except Exception as e:
        return {"status": "error", "reason": f"contract_lookup:{e}", "underlying": underlying}

    # Refuse to submit market SELL when there's no bid — same gate the regular
    # executor uses to avoid the FNDX-class 40310000 retry loop.
    bid, ask = fetch_option_bid_ask(option_data_client(), osym)
    if not (bid and bid > 0):
        return {
            "status": "skipped",
            "reason": "option_no_bid_for_market_sell",
            "bid": bid,
            "ask": ask,
            "option_symbol": osym,
            "underlying": underlying,
        }

    req = MarketOrderRequest(
        symbol=osym,
        qty=float(qty),
        side=OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
        position_intent=PositionIntent.SELL_TO_CLOSE,
    )
    try:
        order = tc.submit_order(req)
    except Exception as e:
        return {"status": "error", "reason": str(e), "option_symbol": osym, "underlying": underlying}
    return {
        "order_id": str(order.id),
        "status": str(order.status),
        "option_qty": qty,
        "option_symbol": osym,
        "underlying": underlying,
        "rationale": rationale,
    }


def _automation_journal_snapshot(ref_price: float, symbol: str) -> dict:
    """Minimal market snapshot for SQLite/JSONL when no LLM cycle ran."""
    a = trading_client().get_account()
    eq = float(a.equity)
    last = float(getattr(a, "last_equity", eq) or eq)
    return {
        "symbol": (symbol or "").upper(),
        "price": float(ref_price) if ref_price and ref_price > 0 else 0.0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "account": {
            "equity": eq,
            "cash": float(getattr(a, "cash", 0) or 0),
            "daily_pnl": eq - last,
            "open_positions": getattr(a, "open_position_count", None),
            "daytrade_count": int(getattr(a, "daytrade_count", 0) or 0),
            "pattern_day_trader": bool(getattr(a, "pattern_day_trader", False)),
        },
        "position": None,
        "indicators": {},
        "data_quality": {
            "missing_critical": [],
            "missing_optional": ["programmed_exit_no_llm_snapshot"],
        },
    }


def _journal_programmed_exit(
    *,
    journal_symbol: str,
    decision: dict,
    execute_result: dict,
    ref_px: float,
    kind: str,
    detail: dict,
) -> None:
    """Mirror scan-cycle journal rows so JSONL/SQLite capture rule-based exits."""
    try:
        snap = _automation_journal_snapshot(ref_px, journal_symbol)
        logic = build_logic_json(
            snap,
            decision,
            decision,
            execute_result,
            debate=None,
            automated_exit={"kind": kind, **detail},
        )
        write_journal_entry(journal_symbol, snap, decision, logic)
    except Exception:
        log.exception(
            "programmed_exit_journal_failed kind=%s symbol=%s",
            kind,
            journal_symbol,
        )


def _enum_str(v: object) -> str:
    return str(getattr(v, "value", v) or "").lower()


def _order_id_already_in_journal(order_id: str) -> bool:
    from trader import config
    from trader.journal import init_db

    init_db()
    if not order_id:
        return True
    try:
        with _db_connect(config.DB_PATH) as c:
            row = c.execute(
                "SELECT 1 FROM trades WHERE order_id = ? LIMIT 1",
                (order_id,),
            ).fetchone()
        return row is not None
    except sqlite3.Error:
        log.warning("journal_order_id_lookup_failed order_id=%s", order_id)
        return True


def _bracket_exit_kind_from_order(order_type: str) -> str | None:
    """Map Alpaca leg type to automated_exit.kind (GTC stock brackets only)."""
    if order_type == "limit":
        return "bracket_take_profit"
    if order_type in ("stop", "stop_limit"):
        return "bracket_stop"
    return None


def sync_bracket_exit_fills() -> None:
    """Journal filled GTC bracket legs (take-profit limit / stop-loss) executed at Alpaca.

    ``run_cycle`` only journals sells the bot submits itself. Bracket children fill
    at the broker with their own ``order_id`` — we poll closed SELLs on the position
    health interval, dedupe against ``trades.order_id``, and append matching rows.
    """
    if os.getenv("BRACKET_FILL_SYNC_DISABLE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return
    try:
        lookback_days = int(os.getenv("BRACKET_FILL_SYNC_LOOKBACK_DAYS", "14") or 14)
    except ValueError:
        lookback_days = 14
    lookback_days = max(1, min(lookback_days, 30))
    after = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    try:
        lim = int(os.getenv("BRACKET_FILL_SYNC_ORDER_LIMIT", "200") or 200)
    except ValueError:
        lim = 200
    lim = max(1, min(lim, 500))

    try:
        from alpaca.trading.enums import OrderSide, QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        tc = trading_client()
        req = GetOrdersRequest(
            status=QueryOrderStatus.CLOSED,
            after=after,
            limit=lim,
            nested=False,
            side=OrderSide.SELL,
        )
        orders = list(tc.get_orders(filter=req))
    except Exception:
        log.exception("bracket_exit_sync_fetch_failed")
        return

    journaled = 0
    for o in orders:
        oid = str(getattr(o, "id", "") or "")
        if not oid or _order_id_already_in_journal(oid):
            continue
        if _enum_str(getattr(o, "side", None)) != "sell":
            continue
        ac = _enum_str(getattr(o, "asset_class", None))
        if ac and ac != "us_equity":
            continue
        sym = (getattr(o, "symbol", None) or "").strip().upper()
        if not sym or parse_occ_us_option_symbol(sym):
            continue
        if getattr(o, "filled_at", None) is None:
            continue
        try:
            fq = float(o.filled_qty or 0)
        except (TypeError, ValueError):
            fq = 0.0
        if fq <= 0:
            continue
        ot = _enum_str(getattr(o, "type", None))
        be_kind = _bracket_exit_kind_from_order(ot)
        if not be_kind:
            continue
        try:
            fap = float(o.filled_avg_price or 0)
        except (TypeError, ValueError):
            fap = 0.0
        rationale = (
            "bracket_take_profit_automation"
            if be_kind == "bracket_take_profit"
            else "bracket_stop_automation"
        )
        dec = {
            "action": "SELL",
            "symbol": sym,
            "strategy": "long_stock",
            "confidence": 1.0,
            "size_pct": 0.0,
            "rationale": rationale,
            "stop_loss": 0.0,
            "take_profit": 0.0,
        }
        ex = {
            "status": _enum_str(getattr(o, "status", None)) or "filled",
            "order_id": oid,
            "filled_qty": fq,
            "filled_avg_price": fap,
            "sync_source": "alpaca_closed_orders_bracket_leg",
        }
        fa = getattr(o, "filled_at", None)
        sa = getattr(o, "submitted_at", None)
        detail = {
            "alpaca_order_type": ot,
            "client_order_id": getattr(o, "client_order_id", None),
            "filled_at": fa.isoformat() if fa else None,
            "submitted_at": sa.isoformat() if sa else None,
        }
        _journal_programmed_exit(
            journal_symbol=sym,
            decision=dec,
            execute_result=ex,
            ref_px=fap,
            kind=be_kind,
            detail=detail,
        )
        try:
            record_day_trade()
        except Exception:
            log.debug("record_day_trade_after_bracket_sync_failed", exc_info=True)
        journaled += 1

    if journaled:
        log.info("bracket_exit_sync_journaled count=%d", journaled)


def _record_position_health_event(payload: dict) -> None:
    """Append a structured `position_health` row to the journal JSONL.

    Why: 5-minute snapshots of peak / floor / ladder progress / expiry countdown
    give the operator real intraday visibility — without this, the only public
    record of position state was the 4×/day scan_cycle and a one-line
    `open_count=N symbols=...` entry. Best-effort; never raises.
    """
    raw = (os.getenv("TRADING_HEALTH_JSONL_PATH") or str(JSONL_PATH)).strip()
    if not raw:
        return
    try:
        path = Path(raw).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, default=str) + "\n")
    except OSError:
        log.debug("position_health_journal_append_failed", exc_info=True)


def run_position_health() -> None:  # noqa: C901  (intentionally long: exit triggers)
    """Periodic position state + exit triggers (no LLM):
      0) Daily P&L giveback halt flag + emergency flatten if daily loss exceeds threshold
      1) Expiry sweep   — close options on T-0 after the configured ET cutoff
      1b) Options hard stop — full close when unrealized % <= OPTIONS_HARD_STOP_PCT
      2) Profit ladder  — partial-close options at +X% / +Y% unrealized rungs
      3) Trail giveback — close on N% giveback from the all-time peak unrealized PnL
      4) Structured journal event with peak / floor / expiry / ladder progress
    """
    now = datetime.now(ET)
    if not is_us_equity_rth(now):
        return
    try:
        giveback = float(os.getenv("TRAIL_GIVEBACK_PCT", "0.40") or 0.40)
    except ValueError:
        giveback = 0.40
    giveback = max(0.0, min(0.95, giveback))
    try:
        _trail_floor_default = float(os.getenv("TRAIL_MIN_TRACK_PNL", "25.0") or 25.0)
    except ValueError:
        _trail_floor_default = 25.0
    _trail_floor_default = max(0.0, _trail_floor_default)
    today_et = now.strftime("%Y-%m-%d")
    today_d = now.date()
    sweep_cutoff = _parse_et_clock(OPTIONS_EXPIRY_SWEEP_TIME_ET)
    sweep_open_now = (now.time() >= sweep_cutoff)
    ladder_pcts = OPTIONS_PROFIT_LADDER_PCT
    ladder_fracs = OPTIONS_PROFIT_LADDER_FRACTION
    ladder_enabled = (
        not OPTIONS_PROFIT_LADDER_DISABLE
        and ladder_pcts
        and ladder_fracs
        and len(ladder_pcts) == len(ladder_fracs)
    )

    try:
        tc = trading_client()
        acc_raw = tc.get_account()
        positions = list(tc.get_all_positions())
    except Exception:
        log.exception("position_health_failed")
        try:
            sync_bracket_exit_fills()
        except Exception:
            log.exception("bracket_exit_sync_after_positions_fetch_failed")
        return

    equity_f = float(acc_raw.equity)
    last_eq_f = float(getattr(acc_raw, "last_equity", None) or acc_raw.equity)
    daily_pnl_f = equity_f - last_eq_f
    start_eq_f = equity_f - daily_pnl_f

    conn_pre = _db_connect(DB_PATH)
    try:
        _ensure_risk_state(conn_pre)
        peak_d = (_risk_kv_get(conn_pre, "daily_pnl_peak_date_et") or "").strip()
        if peak_d != today_et:
            _risk_kv_set(conn_pre, "daily_pnl_peak_date_et", today_et)
            _risk_kv_set(conn_pre, "daily_pnl_peak_value", str(daily_pnl_f))
            _risk_kv_set(conn_pre, "giveback_halt_date_et", "")
        else:
            try:
                pv = float(_risk_kv_get(conn_pre, "daily_pnl_peak_value") or daily_pnl_f)
            except ValueError:
                pv = daily_pnl_f
            new_pv = max(pv, daily_pnl_f)
            _risk_kv_set(conn_pre, "daily_pnl_peak_value", str(new_pv))
            try:
                gb_pct = float(os.getenv("DAILY_GIVEBACK_HALT_PCT", "0.5") or 0.5)
            except ValueError:
                gb_pct = 0.5
            try:
                gb_min = float(os.getenv("DAILY_GIVEBACK_MIN_PEAK_USD", "200") or 200)
            except ValueError:
                gb_min = 200.0
            if new_pv >= gb_min and (new_pv - daily_pnl_f) >= gb_pct * new_pv - 1e-9:
                _risk_kv_set(conn_pre, "giveback_halt_date_et", today_et)
                log.info(
                    "position_health_giveback_halt date=%s peak=%.2f current=%.2f",
                    today_et,
                    new_pv,
                    daily_pnl_f,
                )

        trigger_dl = os.getenv("DAILY_LOSS_TRIGGER_EXIT_ALL", "true").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        try:
            dl_pct = float(
                os.getenv("DAILY_LOSS_EXIT_PCT", os.getenv("MAX_DAILY_LOSS_PCT", "0.02"))
                or 0.02
            )
        except ValueError:
            dl_pct = 0.02
        emergency = (
            trigger_dl and start_eq_f > 0 and daily_pnl_f / start_eq_f <= -dl_pct + 1e-15
        )
        if emergency:
            # Halt until next calendar midnight ET so the bot is always
            # fresh for the following day's open, regardless of what time
            # the emergency fires (a 24h window would block the whole next
            # session if the trigger fires in the afternoon).
            now_et = datetime.now(ET)
            next_midnight_et = (now_et + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            halt_until = next_midnight_et.astimezone(timezone.utc)
            _risk_kv_set(conn_pre, "halt_until_utc", halt_until.isoformat())
            conn_pre.commit()
            log.error(
                "position_health_daily_loss_emergency start_eq=%.2f daily_pnl=%.2f ratio=%.5f",
                start_eq_f,
                daily_pnl_f,
                daily_pnl_f / start_eq_f,
            )
            for p in positions:
                psym = (getattr(p, "symbol", "") or "").upper()
                if not psym:
                    continue
                ac = getattr(p, "asset_class", None)
                ac_val = str(getattr(ac, "value", ac) or "").lower()
                if ac_val not in ("us_equity", "us_option"):
                    ac_val = (
                        "us_option" if parse_occ_us_option_symbol(psym) else "us_equity"
                    )
                try:
                    cur_qty = int(abs(float(getattr(p, "qty", 0) or 0)))
                    cur_px = float(getattr(p, "current_price", None) or 0)
                    ref_px = cur_px if cur_px > 0 else float(
                        getattr(p, "avg_entry_price", 0) or 0
                    )
                except (TypeError, ValueError):
                    continue
                if cur_qty <= 0:
                    continue
                if ac_val == "us_option":
                    parsed_occ = parse_occ_us_option_symbol(psym)
                    if not parsed_occ:
                        continue
                    ex = _submit_option_partial_close(
                        parsed_occ, cur_qty, "emergency_daily_loss_flatten"
                    )
                    if ex.get("order_id"):
                        dec = {
                            "action": "SELL",
                            "symbol": parsed_occ["underlying"],
                            "strategy": parsed_occ["strategy"],
                            "confidence": 1.0,
                            "size_pct": 0.0,
                            "rationale": "emergency_daily_loss_automation",
                            "stop_loss": 0.0,
                            "take_profit": 0.0,
                            "option_expiry": parsed_occ["expiry_iso"],
                            "option_strike": parsed_occ["strike"],
                            "risk_exit_kind": "emergency_daily_loss",
                        }
                        _journal_programmed_exit(
                            journal_symbol=parsed_occ["underlying"],
                            decision=dec,
                            execute_result=ex,
                            ref_px=ref_px,
                            kind="emergency_daily_loss",
                            detail={"occ_symbol": psym, "qty_closed": cur_qty},
                        )
                        record_day_trade()
                elif ac_val == "us_equity":
                    dec = {
                        "action": "SELL",
                        "symbol": psym,
                        "strategy": "long_stock",
                        "confidence": 1.0,
                        "size_pct": 0.0,
                        "rationale": "emergency_daily_loss_automation",
                        "stop_loss": 0.0,
                        "take_profit": 0.0,
                        "risk_exit_kind": "emergency_daily_loss",
                    }
                    ex = execute(dec, ref_px, None)
                    if ex.get("order_id"):
                        _journal_programmed_exit(
                            journal_symbol=psym,
                            decision=dec,
                            execute_result=ex,
                            ref_px=ref_px,
                            kind="emergency_daily_loss",
                            detail={"qty_closed": cur_qty},
                        )
                        record_day_trade()
            try:
                sync_bracket_exit_fills()
            except Exception:
                log.exception("bracket_exit_sync_after_emergency")
            return
        conn_pre.commit()
    finally:
        conn_pre.close()

    syms_open = {(getattr(p, "symbol", "") or "").upper() for p in positions}

    health_rows: list[dict] = []

    conn = _db_connect(DB_PATH)
    try:
        _ensure_trail_table(conn)
        for (sym,) in conn.execute(
            "SELECT symbol FROM position_trail_state"
        ).fetchall():
            if sym not in syms_open:
                conn.execute("DELETE FROM position_trail_state WHERE symbol = ?", (sym,))
        conn.commit()

        for p in positions:
            psym = (getattr(p, "symbol", "") or "").upper()
            if not psym:
                continue
            ac = getattr(p, "asset_class", None)
            ac_val = str(getattr(ac, "value", ac) or "").lower()
            if ac_val not in ("us_equity", "us_option"):
                ac_val = (
                    "us_option" if parse_occ_us_option_symbol(psym) else "us_equity"
                )
            try:
                unreal = float(p.unrealized_pl)
                unreal_pct = float(getattr(p, "unrealized_plpc", None) or 0)
                cur_px = float(getattr(p, "current_price", None) or 0)
                cur_qty = int(abs(float(getattr(p, "qty", 0) or 0)))
                avg_entry = float(getattr(p, "avg_entry_price", 0) or 0)
            except (TypeError, ValueError):
                continue

            mult = 100.0 if ac_val == "us_option" else 1.0
            min_track_pnl = effective_trail_min_track_pnl(abs(cur_qty) * avg_entry * mult)

            row = conn.execute(
                """SELECT peak_unrealized_pnl, asof_date_et, exit_fired,
                          profit_ladder_rungs_fired, expiry_sweep_fired, initial_qty
                   FROM position_trail_state WHERE symbol = ?""",
                (psym,),
            ).fetchone()
            if row:
                peak_stored = float(row[0])
                asof = str(row[1])
                exit_fired_i = int(row[2] or 0)
                rungs_fired = _csv_int_set(str(row[3] or ""))
                expiry_swept = int(row[4] or 0)
                initial_qty = int(row[5] or 0) or cur_qty
            else:
                peak_stored, asof, exit_fired_i = unreal, today_et, 0
                rungs_fired = set()
                expiry_swept = 0
                initial_qty = cur_qty

            # Multi-day peak (default new behavior). When TRAIL_RESET_DAILY=true
            # the legacy intraday-only peak is restored. Profit-ladder + expiry
            # sweep state are NEVER reset by the daily flag — those are sticky
            # by design (we don't want to re-fire the same partial close).
            if TRAIL_RESET_DAILY and asof != today_et:
                peak_stored = unreal
                exit_fired_i = 0

            new_peak = max(peak_stored, unreal)

            parsed_occ = parse_occ_us_option_symbol(psym) if ac_val == "us_option" else None
            days_to_expiry: int | None = None
            if parsed_occ:
                days_to_expiry = (parsed_occ["expiry"] - today_d).days

            # Persist refreshed state BEFORE attempting any exit. If the SELL
            # call raises mid-flight we don't want to lose the new peak / qty
            # baseline on the next tick.
            conn.execute(
                """INSERT INTO position_trail_state
                   (symbol, peak_unrealized_pnl, asof_date_et, exit_fired,
                    profit_ladder_rungs_fired, expiry_sweep_fired, initial_qty)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(symbol) DO UPDATE SET
                     peak_unrealized_pnl = excluded.peak_unrealized_pnl,
                     asof_date_et = excluded.asof_date_et,
                     exit_fired = excluded.exit_fired,
                     initial_qty = CASE
                       WHEN position_trail_state.initial_qty <= 0
                       THEN excluded.initial_qty
                       ELSE position_trail_state.initial_qty
                     END""",
                (
                    psym,
                    new_peak,
                    today_et,
                    exit_fired_i,
                    ",".join(str(i) for i in sorted(rungs_fired)),
                    expiry_swept,
                    initial_qty,
                ),
            )
            conn.commit()

            ref_px = cur_px if cur_px > 0 else float(getattr(p, "avg_entry_price", 0) or 0)
            trail_floor = new_peak * (1.0 - giveback) if new_peak > 0 else None

            health_row = {
                "symbol": psym,
                "asset_class": ac_val,
                "qty": cur_qty,
                "initial_qty": initial_qty,
                "current_price": cur_px,
                "unrealized_pnl": round(unreal, 2),
                "unrealized_pct": round(unreal_pct, 4),
                "peak_unrealized_pnl": round(new_peak, 2),
                "trail_floor": round(trail_floor, 2) if trail_floor is not None else None,
                "trail_giveback_pct": giveback,
                "ladder_rungs_fired": sorted(rungs_fired),
                "ladder_pcts": list(ladder_pcts) if ladder_enabled else [],
                "expiry_swept": bool(expiry_swept),
                "days_to_expiry": days_to_expiry,
                "exit_fired": bool(exit_fired_i),
            }

            # Priority cascade — at most ONE exit action per symbol per tick.
            # Higher priority wins; remaining contracts are re-evaluated next tick.
            action_taken: dict | None = None

            # ---- 1) Expiry sweep (highest priority — forced assignment risk) ----
            if (
                action_taken is None
                and parsed_occ is not None
                and not OPTIONS_EXPIRY_SWEEP_DISABLE
                and not expiry_swept
                and days_to_expiry == 0
                and sweep_open_now
                and cur_qty > 0
            ):
                ex = _submit_option_partial_close(parsed_occ, cur_qty, "expiry_sweep_t0")
                log.info(
                    "position_expiry_sweep symbol=%s expiry=%s qty=%d cutoff=%s result=%s",
                    psym,
                    parsed_occ["expiry_iso"],
                    cur_qty,
                    sweep_cutoff.strftime("%H:%M"),
                    json.dumps(ex, default=str),
                )
                if ex.get("order_id"):
                    dec = {
                        "action": "SELL",
                        "symbol": parsed_occ["underlying"],
                        "strategy": parsed_occ["strategy"],
                        "confidence": 1.0,
                        "size_pct": 0.0,
                        "rationale": "expiry_sweep_automation",
                        "stop_loss": 0.0,
                        "take_profit": 0.0,
                        "option_expiry": parsed_occ["expiry_iso"],
                        "option_strike": parsed_occ["strike"],
                        "risk_exit_kind": "expiry_sweep",
                    }
                    _journal_programmed_exit(
                        journal_symbol=parsed_occ["underlying"],
                        decision=dec,
                        execute_result=ex,
                        ref_px=ref_px,
                        kind="expiry_sweep",
                        detail={
                            "occ_symbol": psym,
                            "expiry_iso": parsed_occ["expiry_iso"],
                            "qty_closed": cur_qty,
                        },
                    )
                    conn.execute(
                        "UPDATE position_trail_state SET expiry_sweep_fired = 1 WHERE symbol = ?",
                        (psym,),
                    )
                    conn.commit()
                    record_day_trade()
                    action_taken = {"kind": "expiry_sweep", "result": ex}
                else:
                    action_taken = {"kind": "expiry_sweep_skipped", "result": ex}

            # ---- 1b) Options hard stop (loser floor; full close) ----
            if action_taken is None and parsed_occ is not None and cur_qty > 0:
                try:
                    hard_stop = float(os.getenv("OPTIONS_HARD_STOP_PCT", "-0.50") or -0.5)
                except ValueError:
                    hard_stop = -0.5
                if hard_stop >= 0:
                    hard_stop = -0.5
                if unreal_pct <= hard_stop + 1e-12:
                    ex = _submit_option_partial_close(
                        parsed_occ, cur_qty, "options_hard_stop_pct"
                    )
                    log.info(
                        "position_hard_stop symbol=%s unreal_pct=%.4f threshold=%.4f result=%s",
                        psym,
                        unreal_pct,
                        hard_stop,
                        json.dumps(ex, default=str),
                    )
                    if ex.get("order_id"):
                        dec = {
                            "action": "SELL",
                            "symbol": parsed_occ["underlying"],
                            "strategy": parsed_occ["strategy"],
                            "confidence": 1.0,
                            "size_pct": 0.0,
                            "rationale": "options_hard_stop_automation",
                            "stop_loss": 0.0,
                            "take_profit": 0.0,
                            "option_expiry": parsed_occ["expiry_iso"],
                            "option_strike": parsed_occ["strike"],
                            "risk_exit_kind": "options_hard_stop",
                        }
                        _journal_programmed_exit(
                            journal_symbol=parsed_occ["underlying"],
                            decision=dec,
                            execute_result=ex,
                            ref_px=ref_px,
                            kind="options_hard_stop",
                            detail={
                                "occ_symbol": psym,
                                "unrealized_pct": unreal_pct,
                                "qty_closed": cur_qty,
                            },
                        )
                        conn.execute(
                            "UPDATE position_trail_state SET exit_fired = 1 WHERE symbol = ?",
                            (psym,),
                        )
                        conn.commit()
                        record_day_trade()
                        action_taken = {"kind": "options_hard_stop", "result": ex}
                    else:
                        action_taken = {"kind": "options_hard_stop_skipped", "result": ex}

            # ---- 2) Profit ladder (mid priority — partial harvest of runners) ----
            if (
                action_taken is None
                and parsed_occ is not None
                and ladder_enabled
                and cur_qty > 0
            ):
                # Find the highest unfired rung that the position now qualifies for.
                # Iterating top-down guarantees that if we leapt past multiple rungs
                # in a single move (e.g. +600% in one tick), the largest rung wins.
                fire_rung: int | None = None
                for i in range(len(ladder_pcts) - 1, -1, -1):
                    if i in rungs_fired:
                        continue
                    if unreal_pct >= float(ladder_pcts[i]):
                        fire_rung = i
                        break
                if fire_rung is not None:
                    qty_to_close = _ladder_qty_to_close(
                        fire_rung, initial_qty, rungs_fired, ladder_fracs, cur_qty
                    )
                    if qty_to_close > 0:
                        ex = _submit_option_partial_close(
                            parsed_occ,
                            qty_to_close,
                            f"profit_ladder_rung_{fire_rung}_pct{ladder_pcts[fire_rung]}",
                        )
                        log.info(
                            "position_profit_ladder symbol=%s rung=%d threshold_pct=%.2f "
                            "unreal_pct=%.2f qty_close=%d initial_qty=%d result=%s",
                            psym,
                            fire_rung,
                            ladder_pcts[fire_rung],
                            unreal_pct,
                            qty_to_close,
                            initial_qty,
                            json.dumps(ex, default=str),
                        )
                        if ex.get("order_id"):
                            dec = {
                                "action": "SELL",
                                "symbol": parsed_occ["underlying"],
                                "strategy": parsed_occ["strategy"],
                                "confidence": 1.0,
                                "size_pct": 0.0,
                                "rationale": "profit_ladder_automation",
                                "stop_loss": 0.0,
                                "take_profit": 0.0,
                                "option_expiry": parsed_occ["expiry_iso"],
                                "option_strike": parsed_occ["strike"],
                                "risk_exit_kind": "profit_ladder",
                            }
                            _journal_programmed_exit(
                                journal_symbol=parsed_occ["underlying"],
                                decision=dec,
                                execute_result=ex,
                                ref_px=ref_px,
                                kind="profit_ladder",
                                detail={
                                    "occ_symbol": psym,
                                    "rung": fire_rung,
                                    "rung_threshold_pct": float(ladder_pcts[fire_rung]),
                                    "unrealized_pct": unreal_pct,
                                    "qty_closed": qty_to_close,
                                },
                            )
                            rungs_fired.add(fire_rung)
                            # When a lower fraction-rung fires, mark every rung at or
                            # below it as fired too — otherwise a later +500% tick
                            # with frac=1.0 would still try to close the (already
                            # closed) 50% slice that fired earlier this same rung.
                            for j in range(fire_rung):
                                rungs_fired.add(j)
                            conn.execute(
                                "UPDATE position_trail_state SET profit_ladder_rungs_fired = ? WHERE symbol = ?",
                                (",".join(str(i) for i in sorted(rungs_fired)), psym),
                            )
                            conn.commit()
                            record_day_trade()
                            action_taken = {"kind": "profit_ladder", "rung": fire_rung, "result": ex}
                        else:
                            action_taken = {"kind": "profit_ladder_skipped", "rung": fire_rung, "result": ex}

            # ---- 3) Trail-giveback (backstop) ----
            trail_fire = (
                action_taken is None
                and not exit_fired_i
                and new_peak > 0
                and new_peak >= min_track_pnl
                and unreal < new_peak * (1.0 - giveback)
                and cur_qty > 0
            )
            if trail_fire:
                if ac_val == "us_equity":
                    dec = {
                        "action": "SELL",
                        "symbol": psym,
                        "strategy": "long_stock",
                        "confidence": 1.0,
                        "size_pct": 0.0,
                        "rationale": "trail_giveback_automation",
                        "stop_loss": 0.0,
                        "take_profit": 0.0,
                        "risk_exit_kind": "trail_giveback",
                    }
                    ex = execute(dec, ref_px, None)
                elif parsed_occ is not None:
                    dec = {
                        "action": "SELL",
                        "symbol": parsed_occ["underlying"],
                        "strategy": parsed_occ["strategy"],
                        "confidence": 1.0,
                        "size_pct": 0.0,
                        "option_expiry": parsed_occ["expiry_iso"],
                        "option_strike": parsed_occ["strike"],
                        "rationale": "trail_giveback_automation",
                        "stop_loss": 0.0,
                        "take_profit": 0.0,
                        "risk_exit_kind": "trail_giveback",
                    }
                    ex = execute(dec, ref_px, None)
                else:
                    log.warning("position_trail_skip_unparsed_option symbol=%s", psym)
                    ex = {"status": "skipped", "reason": "unparsed_option_symbol"}

                log.info(
                    "position_trail_exit symbol=%s peak=%.2f now=%.2f giveback_pct=%.1f result=%s",
                    psym,
                    new_peak,
                    unreal,
                    giveback * 100.0,
                    json.dumps(ex, default=str),
                )
                if ex.get("order_id"):
                    _journal_programmed_exit(
                        journal_symbol=dec["symbol"],
                        decision=dec,
                        execute_result=ex,
                        ref_px=ref_px,
                        kind="trail_giveback",
                        detail={
                            "occ_symbol": psym if ac_val == "us_option" else None,
                            "peak_unrealized_pnl": new_peak,
                            "unrealized_pnl_at_exit": unreal,
                            "trail_floor": trail_floor,
                            "trail_giveback_pct": giveback,
                        },
                    )
                    conn.execute(
                        "UPDATE position_trail_state SET exit_fired = 1 WHERE symbol = ?",
                        (psym,),
                    )
                    conn.commit()
                    record_day_trade()
                    action_taken = {"kind": "trail_giveback", "result": ex}
                else:
                    action_taken = {"kind": "trail_giveback_skipped", "result": ex}

            if action_taken is not None:
                health_row["action"] = action_taken

            health_rows.append(health_row)
    finally:
        conn.close()

    syms = sorted(syms_open)
    log.info(
        "position_health open_count=%d symbols=%s",
        len(positions),
        ",".join(syms[:50]) + ("..." if len(syms) > 50 else ""),
    )
    _record_position_health_event(
        {
            "kind": "position_health",
            "ts_et": now.isoformat(),
            "open_count": len(positions),
            "trail_giveback_pct": giveback,
            "ladder_pcts": list(ladder_pcts) if ladder_enabled else [],
            "expiry_sweep_disabled": OPTIONS_EXPIRY_SWEEP_DISABLE,
            "rows": health_rows,
        }
    )
    try:
        sync_bracket_exit_fills()
    except Exception:
        log.exception("bracket_exit_sync_failed")


def run_eod_summary() -> None:
    """Write dated + LATEST_EOD.md for the dashboard / scripts (no LLM). Mon–Fri 16:05 ET."""
    now_et = datetime.now(ET)
    today_d = now_et.date()
    # Use a strict half-open window [00:00 today, 00:00 next day) so a sub-second
    # journal row at 23:59:59.999 ET is captured without an off-by-µs miss.
    start_et = ET.localize(datetime.combine(today_d, time_of_day.min))
    next_day_start_et = ET.localize(datetime.combine(today_d + timedelta(days=1), time_of_day.min))
    start_u = start_et.astimezone(timezone.utc).isoformat()
    end_u = next_day_start_et.astimezone(timezone.utc).isoformat()

    reports_dir = Path(os.getenv("DATA_REPORTS_DIR", str(REPO_ROOT / "data" / "reports")))
    reports_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, object]] = []
    counts: dict[str, int] = {}
    exec_counts: dict[str, int] = {}
    equity_vals: list[float] = []
    try:
        with _db_connect(DB_PATH, row_factory=sqlite3.Row) as c:
            for r in c.execute(
                """SELECT id, timestamp, symbol, action, execute_status, equity, daily_pnl
                   FROM trades WHERE timestamp >= ? AND timestamp < ?
                   ORDER BY id""",
                (start_u, end_u),
            ):
                d = dict(r)
                rows.append(d)
                act = (str(d.get("action") or "")).upper() or "?"
                counts[act] = counts.get(act, 0) + 1
                es = str(d.get("execute_status") or "null")
                exec_counts[es] = exec_counts.get(es, 0) + 1
                eq = d.get("equity")
                if eq is not None:
                    try:
                        equity_vals.append(float(eq))
                    except (TypeError, ValueError):
                        pass
    except Exception:
        log.exception("eod_summary_db_failed")

    acc: dict[str, float] | None = None
    try:
        a = trading_client().get_account()
        acc = {"equity": float(a.equity), "last_equity": float(a.last_equity)}
    except Exception as e:
        log.warning("eod_summary_account_skip err=%s", e)

    eq_max = max(equity_vals) if equity_vals else None
    eq_last = equity_vals[-1] if equity_vals else None

    lines = [
        f"# EOD summary {today_d.isoformat()} (America/New_York)",
        "",
        f"- Journal rows (ET calendar day): **{len(rows)}**",
        f"- Action counts: `{json.dumps(counts)}`",
        f"- Execute status: `{json.dumps(exec_counts)}`",
    ]
    if eq_max is not None:
        lines.append(f"- Max journal equity today: **{eq_max:,.2f}**")
    if eq_last is not None:
        lines.append(f"- Last journal equity snapshot: **{eq_last:,.2f}**")
    if acc:
        lines.append(
            f"- Alpaca equity (now): **{acc['equity']:,.2f}** "
            f"(session last_equity **{acc['last_equity']:,.2f}**)"
        )
    lines.append("")
    lines.append("Latest EOD digest is mirrored in `LATEST_EOD.md` for ad-hoc readers.")

    body = "\n".join(lines) + "\n"
    out_path = reports_dir / f"eod-{today_d.isoformat()}.md"
    latest = reports_dir / "LATEST_EOD.md"
    out_path.write_text(body, encoding="utf-8")
    latest.write_text(body, encoding="utf-8")
    log.info("eod_summary_written path=%s rows=%d", out_path, len(rows))


def run_cycle() -> None:
    now = datetime.now(ET)
    if not is_us_equity_rth(now):
        return

    t0 = time.perf_counter()
    regime = get_market_regime()
    log.info(
        "market_regime direction=%s conviction=%.2f rationale=%s",
        regime.direction,
        regime.conviction,
        (regime.rationale or "")[:120],
    )
    symbols = get_watchlist(regime=regime)
    log.info("scan_cycle_start time=%s symbols=%s", now.isoformat(), symbols)
    cycle_errors = False
    counts = {"HOLD": 0, "BUY": 0, "SELL": 0, "HALT": 0, "other": 0}
    per_symbol_ms: dict[str, int] = {}
    failed_symbols: list[str] = []
    for symbol in symbols:
        sym_t0 = time.perf_counter()
        try:
            snapshot = get_market_snapshot(symbol, regime=regime)
            brain_out = get_decision(snapshot)
            debate = brain_out.pop("debate", None)
            raw_decision = {**brain_out, **({"debate": debate} if debate is not None else {})}
            decision = validate(
                brain_out,
                snapshot["account"],
                snapshot.get("position"),
                allowed_symbols=symbols,
                snapshot=snapshot,
            )
            if debate and isinstance(debate, dict):
                log.info(
                    "brain_debate symbol=%s mode=%s trader_action=%s final_action=%s",
                    symbol,
                    debate.get("mode") or "-",
                    (debate.get("trader") or {}).get("action"),
                    decision.get("action"),
                )
            action = decision.get("action")
            ac = counts.get(action) if action in counts else None
            if ac is not None:
                counts[action] = ac + 1
            else:
                counts["other"] = counts["other"] + 1

            execute_result = None
            if action in ("BUY", "SELL"):
                atr = snapshot.get("indicators", {}).get("atr_14")
                execute_result = execute(
                    decision,
                    snapshot["price"],
                    float(atr) if atr is not None else None,
                )
                log.info(
                    "execute symbol=%s action=%s strategy=%s result=%s",
                    symbol,
                    action,
                    decision.get("strategy"),
                    json.dumps(execute_result, default=str),
                )
                if execute_result.get("clamped_stop") is not None:
                    decision = {
                        **decision,
                        "stop_loss": execute_result["clamped_stop"],
                        "take_profit": execute_result["clamped_take_profit"],
                    }
                if execute_result.get("order_id"):
                    record_day_trade()

            logic = build_logic_json(
                snapshot, raw_decision, decision, execute_result, debate=debate
            )
            write_journal_entry(symbol, snapshot, decision, logic)
        except Exception:
            cycle_errors = True
            failed_symbols.append(symbol)
            log.exception("symbol=%s", symbol)
        finally:
            per_symbol_ms[symbol] = int(round((time.perf_counter() - sym_t0) * 1000))

    elapsed = round(time.perf_counter() - t0, 3)
    # Top-5 slowest symbols help spot a single hung enrichment source after the fact
    slowest_5 = sorted(per_symbol_ms.items(), key=lambda kv: kv[1], reverse=True)[:5]
    summary = {
        "kind": "scan_cycle",
        "time_et": now.isoformat(),
        "symbols_scanned": len(symbols),
        "elapsed_sec": elapsed,
        "cycle_errors": cycle_errors,
        "action_counts": {k: v for k, v in counts.items() if v},
        "failed_symbols": failed_symbols,
        "slowest_symbols_ms": dict(slowest_5),
        "jsonl_path": str(JSONL_PATH),
        "market_regime": {
            "direction": regime.direction,
            "conviction": regime.conviction,
            "bearish_vehicles": list(regime.bearish_vehicles),
        },
        "trading_profile": TRADING_PROFILE,
    }
    log.info("cycle_summary %s", json.dumps(summary, default=str))
    _append_metrics_line(summary)
    ping_healthcheck(ok=not cycle_errors)


def _parse_extra_cron_slots() -> list[tuple[int, int]]:
    raw = (os.getenv("SCHEDULE_EXTRA_SLOTS") or "").strip()
    if not raw:
        return [(11, 30), (13, 30)]
    out: list[tuple[int, int]] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            continue
        a, b = part.split(":", 1)
        try:
            out.append((int(a.strip()), int(b.strip())))
        except ValueError:
            continue
    return out


def main() -> None:
    check_live_profile_guard(paper=ALPACA_PAPER)
    scheduler = BlockingScheduler(timezone=ET)
    # Reduced frequency: 4 times a day during market hours to save on API credits
    # 10:00 (Open/Early), 12:00 (Mid-day), 14:00 (Pre-close), 15:30 (Market Close Prep)
    # NOTE: each CronTrigger pins timezone=ET explicitly. APScheduler's per-trigger
    # default-tz inheritance from the scheduler is unreliable in some envs and has
    # bitten us before (cron resolved against UTC, scans only firing pre-noon ET).
    market_slots = [(10, 0), (12, 0), (14, 0), (15, 30)]
    scheduler.add_job(
        run_cycle,
        OrTrigger([
            CronTrigger(
                day_of_week="mon-fri",
                hour=str(h),
                minute=str(m),
                jitter=60,
                timezone=ET,
            )
            for h, m in market_slots
        ]),
        coalesce=True,
        max_instances=1,
        misfire_grace_time=300,
    )
    log.info(
        "scheduler_started frequency=4x_daily jitter_sec=60 rht_only=true tz=America/New_York slots=%s",
        market_slots,
    )

    scheduler.add_job(
        run_eod_summary,
        CronTrigger(
            day_of_week="mon-fri",
            hour=16,
            minute=5,
            jitter=30,
            timezone=ET,
        ),
        coalesce=True,
        max_instances=1,
        misfire_grace_time=300,
    )
    log.info("scheduler_eod_summary time=16:05_ET Mon-Fri jitter_sec=30")

    if os.getenv("SCHEDULE_EXTRA_ENABLE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        extra_slots = _parse_extra_cron_slots()
        for hour, minute in extra_slots:
            scheduler.add_job(
                run_cycle,
                CronTrigger(
                    day_of_week="mon-fri",
                    hour=str(hour),
                    minute=str(minute),
                    jitter=60,
                    timezone=ET,
                ),
                coalesce=True,
                max_instances=1,
                misfire_grace_time=300,
            )
        log.info("scheduler_extra_slots_enabled slots=%s", extra_slots)

    try:
        health_min = int(os.getenv("POSITION_HEALTH_INTERVAL_MIN", "5") or "5")
    except ValueError:
        health_min = 5
    if health_min > 0:
        scheduler.add_job(
            run_position_health,
            IntervalTrigger(minutes=health_min),
            coalesce=True,
            max_instances=1,
            misfire_grace_time=120,
        )
        log.info("position_health_interval_min=%d", health_min)

    prewarm_caches()
    if is_us_equity_rth(datetime.now(ET)):
        run_cycle()
    scheduler.start()


if __name__ == "__main__":
    main()
