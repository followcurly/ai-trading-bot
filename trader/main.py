"""v2 red-day buy & hold — per-ticker red + underweight sleeve → market buy.

No LLM. No auto-sell. Scans twice daily (default 10:30 + 15:30 ET) so late-day
red can still trigger buys. Same sleeve is not bought twice in one session.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

load_dotenv()

from trader.allocation import red_buy_candidates, sleeve_weights
from trader.alpaca_runtime import trading_client
from trader.config import (
    ALPACA_PAPER,
    DB_PATH,
    RED_DAY_BUY_PCT,
    RED_DAY_SCAN_TIMES_ET,
    REPO_ROOT,
    TRADING_PROFILE,
)
from trader.db import connect as db_connect
from trader.executor_simple import market_buy_notional
from trader.funds import load_funds
from trader.journal import build_logic_json, write_journal_entry
from trader.market_hours import ET, is_us_equity_rth
from trader.signal_red_day import day_signal

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("trader.main")

_scheduler: BlockingScheduler | None = None


def _handle_signal(signum: int, _frame: object) -> None:
    log.info("received signal %s — shutting down", signum)
    sched = _scheduler
    if sched is not None and sched.running:
        sched.shutdown(wait=False)


def _parse_scan_hhmm(raw: str) -> tuple[int, int]:
    parts = (raw or "10:30").strip().split(":")
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
    except (ValueError, IndexError):
        return 10, 30
    return max(0, min(23, h)), max(0, min(59, m))


def _parse_scan_slots(raw: str) -> list[tuple[int, int]]:
    slots: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        hm = _parse_scan_hhmm(part)
        if hm not in seen:
            seen.add(hm)
            slots.append(hm)
    return slots or [(10, 30), (15, 30)]


def _account_snapshot() -> dict:
    tc = trading_client()
    acct = tc.get_account()
    positions = list(tc.get_all_positions())
    equity = float(acct.equity)
    cash = float(acct.cash)
    last_eq = float(getattr(acct, "last_equity", equity) or equity)
    return {
        "account": {
            "equity": equity,
            "cash": cash,
            "daily_pnl": equity - last_eq,
            "open_positions": len(positions),
            "daytrade_count": int(getattr(acct, "daytrade_count", 0) or 0),
            "pattern_day_trader": bool(getattr(acct, "pattern_day_trader", False)),
        },
        "positions": positions,
        "equity": equity,
        "cash": cash,
    }


def _todays_placed_buys_et(now: datetime) -> tuple[set[str], set[str]]:
    """Placed BUYs whose journal timestamp falls on today's ET calendar date."""
    day = now.astimezone(ET).date()
    symbols: set[str] = set()
    sleeves: set[str] = set()
    if not DB_PATH.is_file():
        return symbols, sleeves
    try:
        with db_connect() as conn:
            rows = conn.execute(
                """
                SELECT timestamp, symbol, logic_json FROM trades
                WHERE action = 'BUY' AND execute_status = 'placed'
                """
            ).fetchall()
    except Exception:
        log.exception("failed reading today's buys")
        return symbols, sleeves

    for ts, sym, logic_s in rows:
        try:
            raw = str(ts or "")
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                from datetime import timezone as _tz

                dt = dt.replace(tzinfo=_tz.utc)
            if dt.astimezone(ET).date() != day:
                continue
        except Exception:
            continue
        if sym:
            symbols.add(str(sym).upper())
        if logic_s:
            try:
                logic = json.loads(logic_s)
                sleeve = (logic.get("raw_decision") or {}).get("sleeve")
                if sleeve:
                    sleeves.add(str(sleeve))
            except Exception:
                pass
    return symbols, sleeves


def _journal_hold(symbol: str, snap: dict, rationale: str, meta: dict) -> None:
    decision = {
        "action": "HOLD",
        "strategy": "buy_hold_etf",
        "confidence": None,
        "size_pct": None,
        "stop_loss": None,
        "take_profit": None,
        "rationale": rationale,
    }
    logic = build_logic_json(
        snapshot=snap,
        raw_decision=decision,
        final_decision=decision,
        execute_result={"status": "not_attempted", "reason": rationale, **meta},
    )
    write_journal_entry(symbol, snap, decision, logic)


def _journal_buy(
    symbol: str, snap: dict, rationale: str, execute_result: dict, meta: dict
) -> None:
    decision = {
        "action": "BUY",
        "strategy": "buy_hold_etf",
        "confidence": 1.0,
        "size_pct": RED_DAY_BUY_PCT,
        "stop_loss": None,
        "take_profit": None,
        "rationale": rationale,
    }
    logic = build_logic_json(
        snapshot=snap,
        raw_decision={**decision, **meta},
        final_decision=decision,
        execute_result=execute_result,
    )
    write_journal_entry(symbol, snap, decision, logic)


def run_cycle(*, force: bool = False, dry_run: bool = False) -> dict:
    """Run one red-day scan. Outside RTH is a no-op unless ``force``."""
    now = datetime.now(ET)
    if not force and not is_us_equity_rth(now):
        log.info("run_cycle skip — outside RTH (%s)", now.isoformat())
        return {"skipped": True, "reason": "outside_rth"}

    dry = dry_run or os.getenv("RED_DAY_DRY_RUN", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    uni = load_funds()
    ctx = _account_snapshot()
    equity = ctx["equity"]
    cash = ctx["cash"]
    positions = ctx["positions"]
    bought_syms, bought_sleeves = _todays_placed_buys_et(now)
    scan_label = "afternoon" if now.hour >= 13 else "morning"
    base_snap = {
        "symbol": None,
        "price": None,
        "timestamp": now.isoformat(),
        "account": ctx["account"],
        "position": None,
        "open_orders": [],
        "indicators": {},
    }

    weights = sleeve_weights(equity, positions, uni)
    log.info(
        "run_cycle start scan=%s equity=%.2f cash=%.2f paper=%s profile=%s "
        "sleeves=%s already_bought_sleeves=%s dry_run=%s",
        scan_label,
        equity,
        cash,
        ALPACA_PAPER,
        TRADING_PROFILE,
        {w.sleeve.name: round(w.weight, 4) for w in weights},
        sorted(bought_sleeves),
        dry,
    )

    buys = 0
    holds = 0
    skipped_dup = 0
    candidates = red_buy_candidates(equity, positions, uni)
    # Drop sleeves/symbols already filled today (afternoon catch-up stays smart)
    filtered = []
    for cand in candidates:
        if cand.sleeve.name in bought_sleeves or cand.symbol in bought_syms:
            skipped_dup += 1
            ret_s = (
                f"{cand.signal.day_return * 100:.2f}%"
                if cand.signal.day_return is not None
                else "n/a"
            )
            reason = (
                f"ticker_red day_ret={ret_s} sleeve={cand.sleeve.name} "
                f"skip=already_bought_today scan={scan_label}"
            )
            snap = {
                **base_snap,
                "symbol": cand.symbol,
                "price": cand.signal.last,
            }
            _journal_hold(
                cand.symbol,
                snap,
                reason,
                {
                    "day_return": cand.signal.day_return,
                    "is_red": True,
                    "sleeve": cand.sleeve.name,
                    "scan": scan_label,
                    "skip": "already_bought_today",
                },
            )
            holds += 1
            continue
        filtered.append(cand)
    candidates = filtered
    cand_syms = {c.symbol for c in candidates}

    for sleeve in uni.sleeves:
        sym = sleeve.primary
        if sym in cand_syms:
            continue
        if sleeve.name in bought_sleeves:
            continue
        sig = day_signal(sym)
        ret_s = (
            f"{sig.day_return * 100:.2f}%" if sig.day_return is not None else "n/a"
        )
        # Morning: journal primary HOLD for visibility.
        # Afternoon: only journal if newly red (missed morning) but not buying.
        if scan_label == "afternoon" and not sig.is_red:
            continue
        if sig.is_red:
            reason = (
                f"ticker_red day_ret={ret_s} sleeve={sleeve.name} "
                f"skip=sleeve_at_target_or_no_buy scan={scan_label}"
            )
        else:
            reason = (
                f"ticker_green day_ret={ret_s} sleeve={sleeve.name} "
                f"skip scan={scan_label}"
            )
        snap = {**base_snap, "symbol": sym, "price": sig.last}
        _journal_hold(
            sym,
            snap,
            reason,
            {
                "day_return": sig.day_return,
                "is_red": sig.is_red,
                "sleeve": sleeve.name,
                "scan": scan_label,
            },
        )
        holds += 1

    remaining_cash = cash
    for cand in candidates:
        if remaining_cash < 10:
            break
        sig = cand.signal
        ret_s = (
            f"{sig.day_return * 100:.2f}%" if sig.day_return is not None else "n/a"
        )
        rationale = (
            f"ticker_red day_ret={ret_s} sleeve={cand.sleeve.name} "
            f"buy={cand.symbol} gap={cand.sleeve_weight.gap_pct * 100:.1f}% "
            f"scan={scan_label}"
        )
        snap = {
            **base_snap,
            "symbol": cand.symbol,
            "price": sig.last,
            "account": {
                **ctx["account"],
                "cash": remaining_cash,
            },
        }
        result = market_buy_notional(
            cand.symbol,
            equity=equity,
            cash=remaining_cash,
            dry_run=dry,
        )
        _journal_buy(
            cand.symbol,
            snap,
            rationale,
            result,
            {
                "day_return": sig.day_return,
                "sleeve": cand.sleeve.name,
                "gap_pct": cand.sleeve_weight.gap_pct,
                "scan": scan_label,
            },
        )
        if result.get("status") == "placed":
            buys += 1
            bought_sleeves.add(cand.sleeve.name)
            bought_syms.add(cand.symbol)
            remaining_cash = max(
                0.0, remaining_cash - float(result.get("notional") or 0)
            )
        log.info(
            "candidate %s → %s notional=%s scan=%s",
            cand.symbol,
            result.get("status"),
            result.get("notional"),
            scan_label,
        )

    summary = {
        "skipped": False,
        "scan": scan_label,
        "buys": buys,
        "holds": holds,
        "candidates": len(candidates),
        "skipped_already_bought": skipped_dup,
        "equity": equity,
        "dry_run": dry,
    }
    log.info("run_cycle done %s", summary)
    return summary


def main() -> None:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    slots = _parse_scan_slots(RED_DAY_SCAN_TIMES_ET)
    log.info(
        "red_day_v2 starting repo=%s paper=%s scans=%s ET buy_pct=%.2f",
        REPO_ROOT,
        ALPACA_PAPER,
        ",".join(f"{h:02d}:{m:02d}" for h, m in slots),
        RED_DAY_BUY_PCT,
    )

    try:
        tc = trading_client()
        a = tc.get_account()
        log.info(
            "alpaca health status=%s equity=%s cash=%s positions=%s",
            a.status,
            a.equity,
            a.cash,
            len(tc.get_all_positions()),
        )
    except Exception:
        log.exception("alpaca health check failed")

    global _scheduler
    scheduler = BlockingScheduler(timezone=str(ET))
    _scheduler = scheduler
    for i, (hour, minute) in enumerate(slots):
        scheduler.add_job(
            run_cycle,
            CronTrigger(
                day_of_week="mon-fri",
                hour=hour,
                minute=minute,
                timezone=str(ET),
            ),
            id=f"red_day_scan_{i}_{hour:02d}{minute:02d}",
            max_instances=1,
            coalesce=True,
        )
    log.info(
        "scheduler_started red_day_scans %s ET Mon-Fri",
        ",".join(f"{h:02d}:{m:02d}" for h, m in slots),
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)
        _scheduler = None
    log.info("stopped")
    sys.exit(0)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "once":
        force = "--force" in sys.argv
        dry = "--dry-run" in sys.argv
        print(run_cycle(force=force, dry_run=dry))
    else:
        main()
