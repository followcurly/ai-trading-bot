"""Post-model validation: deterministic guardrails after Claude.

Risk tiers (see also .env.example):
  **Tier 1 — kill-switches (always enforced):** invalid equity; daily loss vs equity
    HALT; drawdown-from-peak HALT; malformed options (expiry/strike); invalid strategy/symbol
    allowlists for executable paths.
  **Tier 2 — policy (regime-aware and/or soft mode):** confidence floor (optionally
    scaled by VIX regime from snapshot); concurrent BUY cap; symbol post-SELL cooldown;
    PDT ceiling uses Alpaca ``account.daytrade_count`` as the source of truth;
    the SQLite ``pdt_count`` is auxiliary telemetry logged on divergence.
    size_pct clamp to MAX_POSITION_PCT; optional sector
    notional cap vs snapshot ``portfolio_context.sector_notional_pct``.

`RISK_SOFT_POLICY=true` downgrades selected Tier-2 HOLDs to warnings (`risk_warnings`)
while leaving Tier-1 unchanged. Claude still does not override Tier-1.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any

log = logging.getLogger("trader.risk_engine")

from trader.config import DB_PATH
from trader.db import connect as _db_connect
from trader.market_hours import ET
from trader.options_exec import parse_expiry_date, parse_occ_us_option_symbol
from trader.profile import get_profile

MAX_POSITION_PCT = float(os.getenv("MAX_POSITION_PCT", "0.05"))
MAX_DAILY_LOSS_PCT = float(os.getenv("MAX_DAILY_LOSS_PCT", "0.02"))
MIN_CONFIDENCE = float(os.getenv("MIN_CONFIDENCE", "0.65"))
MAX_DRAWDOWN_FROM_PEAK_PCT = float(os.getenv("MAX_DRAWDOWN_FROM_PEAK_PCT", "0.15"))
SYMBOL_COOLDOWN_HOURS = float(os.getenv("SYMBOL_COOLDOWN_HOURS", "24"))
# Minimum hold time before allowing a SELL on options positions (prevents flip-flop churn).
# Bypass when unrealized_pct >= PROFIT_LOCK_PCT (see validate()).
OPTIONS_MIN_HOLD_HOURS = float(os.getenv("OPTIONS_MIN_HOLD_HOURS", "0.5"))
# If unrealized P&L % on the contract is at or above this, bypass min-hold.
try:
    PROFIT_LOCK_PCT = float((os.getenv("PROFIT_LOCK_PCT") or "0.20").strip() or "0.20")
except ValueError:
    PROFIT_LOCK_PCT = 0.20
# 0 = no limit (allow any number of concurrent longs). Default 1 = one open position at a time.
_MAX_CONCURRENT_BUYS_RAW = int(os.getenv("MAX_CONCURRENT_BUYS", "1"))
MAX_CONCURRENT_BUYS = max(0, _MAX_CONCURRENT_BUYS_RAW)
MAX_ADD_TO_POSITION = os.getenv("MAX_ADD_TO_POSITION", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

try:
    MAX_SECTOR_NOTIONAL_PCT = float(os.getenv("MAX_SECTOR_NOTIONAL_PCT", "0") or 0)
except ValueError:
    MAX_SECTOR_NOTIONAL_PCT = 0.0

try:
    MAX_UNKNOWN_SECTOR_PCT = float(os.getenv("MAX_UNKNOWN_SECTOR_PCT", "0.05") or 0.05)
except ValueError:
    MAX_UNKNOWN_SECTOR_PCT = 0.05

try:
    MIN_OPTION_DTE = int(os.getenv("MIN_OPTION_DTE", "7") or 7)
except ValueError:
    MIN_OPTION_DTE = 7
MIN_OPTION_DTE = max(0, MIN_OPTION_DTE)

MIN_HOLD_OVERNIGHT = os.getenv("MIN_HOLD_OVERNIGHT", "true").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)

try:
    EARNINGS_BLACKOUT_DAYS = int(os.getenv("EARNINGS_BLACKOUT_DAYS", "2") or 2)
except ValueError:
    EARNINGS_BLACKOUT_DAYS = 2
EARNINGS_BLACKOUT_DAYS = max(0, EARNINGS_BLACKOUT_DAYS)

try:
    RSI_EXTREME_FLOOR = float(os.getenv("RSI_EXTREME_FLOOR", "85") or 85)
except ValueError:
    RSI_EXTREME_FLOOR = 85.0

try:
    ADX_TREND_FLOOR = float(os.getenv("ADX_TREND_FLOOR", "30") or 30)
except ValueError:
    ADX_TREND_FLOOR = 30.0

try:
    IV_RANK_CRUSH_FLOOR = float(os.getenv("IV_RANK_CRUSH_FLOOR", "0.85") or 0.85)
except ValueError:
    IV_RANK_CRUSH_FLOOR = 0.85

try:
    PDT_DAYTRADE_CEILING = int(os.getenv("PDT_DAYTRADE_CEILING", "3") or 3)
except ValueError:
    PDT_DAYTRADE_CEILING = 3
PDT_DAYTRADE_CEILING = max(0, PDT_DAYTRADE_CEILING)

try:
    PDT_EQUITY_FLOOR = float(os.getenv("PDT_EQUITY_FLOOR", "25000") or 25_000)
except ValueError:
    PDT_EQUITY_FLOOR = 25_000.0

# SELLs from these automation kinds bypass min-hold-overnight (set on decision by main.py).
RISK_EXIT_KIND_OVERNIGHT_BYPASS = frozenset(
    {
        "profit_ladder",
        "trail_giveback",
        "expiry_sweep",
        "options_hard_stop",
        "emergency_daily_loss",
        "bracket_stop",
        "bracket_take_profit",
    }
)

RISK_SOFT_POLICY = os.getenv("RISK_SOFT_POLICY", "").strip().lower() in (
    "1",
    "true",
    "yes",
    "on",
)
RISK_VIX_REGIME_CONFIDENCE = os.getenv(
    "RISK_VIX_REGIME_CONFIDENCE", ""
).strip().lower() in ("1", "true", "yes", "on")
RISK_VIX_REGIME_COOLDOWN = os.getenv(
    "RISK_VIX_REGIME_COOLDOWN", ""
).strip().lower() in ("1", "true", "yes", "on")

def allowed_strategies() -> tuple[str, ...]:
    return get_profile().allowed_strategies


def _env_float(name: str, default: float | None = None) -> float | None:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _vix_regime(snapshot: dict | None) -> str:
    if not snapshot:
        return ""
    vix = snapshot.get("vix")
    if not isinstance(vix, dict):
        return ""
    r = (vix.get("regime") or "").strip().lower()
    return r if r in ("calm", "normal", "elevated", "stress") else ""


def _effective_min_confidence(snapshot: dict | None) -> float:
    if not RISK_VIX_REGIME_CONFIDENCE:
        return MIN_CONFIDENCE
    regime = _vix_regime(snapshot)
    base = MIN_CONFIDENCE
    if regime == "calm":
        v = _env_float("MIN_CONFIDENCE_CALM")
        if v is not None:
            return max(0.5, min(0.95, v))
        return max(0.5, min(0.95, base - 0.03))
    if regime == "normal":
        v = _env_float("MIN_CONFIDENCE_NORMAL")
        return max(0.5, min(0.95, v if v is not None else base))
    if regime == "elevated":
        v = _env_float("MIN_CONFIDENCE_ELEVATED")
        if v is not None:
            return max(0.5, min(0.95, v))
        return max(0.5, min(0.95, base + 0.02))
    if regime == "stress":
        v = _env_float("MIN_CONFIDENCE_STRESS")
        if v is not None:
            return max(0.5, min(0.95, v))
        return max(0.5, min(0.95, base + 0.05))
    return base


def _cooldown_multiplier(snapshot: dict | None) -> float:
    if not RISK_VIX_REGIME_COOLDOWN:
        return 1.0
    regime = _vix_regime(snapshot)
    key = {
        "calm": "COOLDOWN_MULT_CALM",
        "normal": "COOLDOWN_MULT_NORMAL",
        "elevated": "COOLDOWN_MULT_ELEVATED",
        "stress": "COOLDOWN_MULT_STRESS",
    }.get(regime, "")
    if not key:
        return 1.0
    m = _env_float(key)
    if m is not None and m > 0:
        return m
    defaults = {"calm": 0.75, "normal": 1.0, "elevated": 1.1, "stress": 1.25}
    return defaults.get(regime, 1.0)


def _effective_cooldown_hours(snapshot: dict | None) -> float:
    return max(0.0, SYMBOL_COOLDOWN_HOURS * _cooldown_multiplier(snapshot))


def _ensure_warnings(d: dict[str, Any]) -> list[str]:
    raw = d.get("risk_warnings")
    if isinstance(raw, list):
        out: list[str] = [str(x) for x in raw]
        d["risk_warnings"] = out
        return out
    out2: list[str] = []
    d["risk_warnings"] = out2
    return out2


def _warn(d: dict[str, Any], msg: str) -> None:
    lst = _ensure_warnings(d)
    lst.append(msg)


def _with_hold(decision: dict, reason: str) -> dict:
    """Preserve model fields in the journal when risk forces HOLD."""
    return {**decision, "action": "HOLD", "reason": reason}


def _with_halt(decision: dict, reason: str) -> dict:
    return {**decision, "action": "HALT", "reason": reason}


def _today_et() -> date:
    return datetime.now(ET).date()


def _utc_to_et_date(ts: datetime) -> date:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(ET).date()


def _risk_exit_kind(decision: dict) -> str | None:
    raw = (decision.get("risk_exit_kind") or "").strip().lower()
    return raw or None


def _profit_lock_bypasses_min_hold(u_pct: float | None) -> bool:
    return u_pct is not None and u_pct >= PROFIT_LOCK_PCT


def _options_unrealized_pct_for_decision(pos: dict, decision: dict) -> float | None:
    """Best P&L% on the open option leg matching this SELL decision (underlying snapshot)."""
    opts = pos.get("open_options") or []
    if not isinstance(opts, list) or not opts:
        return None
    strat = decision.get("strategy")
    cp = "C" if strat == "long_call" else "P" if strat == "long_put" else None
    if not cp:
        return None
    exp = parse_expiry_date(decision.get("option_expiry"))
    try:
        strike = float(decision.get("option_strike") or 0)
        strike_key = f"{int(round(strike * 1000)):08d}"
    except (TypeError, ValueError):
        strike_key = ""
    ymd = exp.strftime("%y%m%d") if exp else ""

    for o in opts:
        if not isinstance(o, dict):
            continue
        try:
            pct = float(o.get("unrealized_pct") or 0)
        except (TypeError, ValueError):
            continue
        sym = (o.get("symbol") or "").upper()
        if ymd and strike_key and ymd in sym and cp in sym and strike_key in sym:
            return pct
    return None


def _ensure_risk_state(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS risk_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )"""
    )


def _risk_kv_get(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute(
        "SELECT value FROM risk_state WHERE key = ?", (key,)
    ).fetchone()
    return row[0] if row else None


def _risk_kv_set(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        """INSERT INTO risk_state(key, value) VALUES(?, ?)
           ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
        (key, value),
    )


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    s = raw.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _last_journal_buy_utc(symbol: str) -> datetime | None:
    """Return UTC timestamp of the most recent BUY journal entry for ``symbol`` or
    a *true* OCC option on the same underlying.

    The previous ``LIKE 'CAT%'`` filter false-matched siblings like ``CATY`` /
    ``CATO``, which could spuriously block a CAT options SELL via the min-hold
    guard. We now scan the most recent BUYs for either the exact ticker or any
    journal symbol whose OCC parse resolves to the same underlying.
    """
    if not DB_PATH.exists():
        return None
    sym = symbol.strip().upper()
    try:
        conn = _db_connect(DB_PATH)
        # Pull a small window of recent BUY rows and filter precisely in Python.
        # Cap at 200 to keep this cheap even on large journals.
        rows = conn.execute(
            """SELECT timestamp, symbol FROM trades
               WHERE (UPPER(symbol) = ? OR UPPER(symbol) LIKE ?)
               AND UPPER(COALESCE(action,'')) = 'BUY'
               ORDER BY id DESC LIMIT 200""",
            (sym, sym + "%"),
        ).fetchall()
        conn.close()
    except sqlite3.OperationalError:
        return None
    chosen_ts: str | None = None
    for ts, journal_sym in rows:
        js = (journal_sym or "").upper().strip()
        if js == sym:
            chosen_ts = ts
            break
        parsed = parse_occ_us_option_symbol(js)
        if parsed is not None and str(parsed.get("underlying") or "").upper() == sym:
            chosen_ts = ts
            break
    if not chosen_ts:
        return None
    dt = _parse_ts(chosen_ts)
    if dt and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _last_journal_sell_utc(symbol: str) -> datetime | None:
    if not DB_PATH.exists():
        return None
    try:
        conn = _db_connect(DB_PATH)
        row = conn.execute(
            """SELECT timestamp FROM trades
               WHERE symbol = ? AND UPPER(COALESCE(action,'')) = 'SELL'
               ORDER BY id DESC LIMIT 1""",
            (symbol.upper(),),
        ).fetchone()
        conn.close()
    except sqlite3.OperationalError:
        return None
    if not row:
        return None
    dt = _parse_ts(row[0])
    if dt and dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def validate(
    decision: dict,
    account: dict,
    position: dict | None = None,
    allowed_symbols: list[str] | None = None,
    snapshot: dict[str, Any] | None = None,
) -> dict:
    equity = float(account["equity"])
    daily_pnl = float(account["daily_pnl"])

    if equity <= 0:
        return _with_hold(decision, "Invalid equity")

    start_eq = equity - daily_pnl
    if start_eq > 0 and daily_pnl / start_eq < -MAX_DAILY_LOSS_PCT:
        return _with_halt(
            decision,
            f"Daily loss circuit breaker ({daily_pnl:.2f} vs start-of-day ~{start_eq:.2f})",
        )

    min_conf = _effective_min_confidence(snapshot)
    if decision.get("confidence", 0) < min_conf:
        return _with_hold(
            decision,
            f"Confidence below threshold (need ≥{min_conf:.2f})",
        )

    sym = (decision.get("symbol") or "").strip().upper()
    # Universe is the current cycle's watchlist (held underlyings are always
    # injected via WATCHLIST_INCLUDE_OPEN_POSITIONS so we can SELL what we own).
    if allowed_symbols:
        upper = {s.strip().upper() for s in allowed_symbols if s and s.strip()}
        if sym not in upper:
            return _with_hold(decision, f"{sym} not in current cycle watchlist")

    if decision.get("strategy") not in allowed_strategies():
        return _with_hold(
            decision,
            f"Strategy {decision.get('strategy')} not approved",
        )

    out: dict[str, Any] = {**decision, "symbol": sym}
    if float(out.get("size_pct", 0) or 0) > MAX_POSITION_PCT:
        out["size_pct"] = MAX_POSITION_PCT

    pos = position or {}
    if (
        out.get("action") == "BUY"
        and out.get("strategy") == "long_stock"
        and pos.get("held")
        and not MAX_ADD_TO_POSITION
    ):
        return _with_hold(
            out,
            "Already long this symbol (set MAX_ADD_TO_POSITION=true to add)",
        )

    today_et = _today_et()

    if out.get("strategy") in ("long_call", "long_put") and out.get("action") in (
        "BUY",
        "SELL",
    ):
        exp = parse_expiry_date(out.get("option_expiry"))
        if exp is None:
            return _with_hold(
                out,
                "Missing or invalid option_expiry (YYYY-MM-DD required for options)",
            )
        if exp < today_et:
            return _with_hold(out, "option_expiry is in the past")
        try:
            strike = float(out.get("option_strike"))
        except (TypeError, ValueError):
            return _with_hold(out, "Invalid option_strike for options")
        if strike <= 0:
            return _with_hold(out, "Invalid option_strike for options")
        out["option_expiry"] = exp.isoformat()
        out["option_strike"] = strike

        if (
            out.get("action") == "BUY"
            and MIN_OPTION_DTE > 0
            and (exp - today_et).days < MIN_OPTION_DTE
        ):
            return _with_hold(
                out,
                f"min_dte: option expires in {(exp - today_et).days}d; need ≥{MIN_OPTION_DTE}d",
            )

    if out.get("action") == "BUY" and snapshot and EARNINGS_BLACKOUT_DAYS >= 0:
        eda = snapshot.get("earnings_days_away")
        if eda is not None:
            try:
                eda_i = int(eda)
            except (TypeError, ValueError):
                eda_i = 9999
            if 0 <= eda_i <= EARNINGS_BLACKOUT_DAYS:
                return _with_hold(
                    out,
                    f"earnings_blackout: earnings_days_away={eda_i} (≤{EARNINGS_BLACKOUT_DAYS})",
                )

    if out.get("action") == "BUY" and snapshot:
        ind = snapshot.get("indicators") or {}
        try:
            rsi = float(ind.get("rsi_14"))
            adx = float(ind.get("adx_14"))
        except (TypeError, ValueError):
            rsi = adx = None
        if (
            rsi is not None
            and adx is not None
            and rsi >= RSI_EXTREME_FLOOR
            and adx < ADX_TREND_FLOOR
        ):
            return _with_hold(
                out,
                f"rsi_extreme_no_trend: rsi={rsi:.1f} adx={adx:.1f}",
            )

    if (
        out.get("action") == "BUY"
        and snapshot
        and out.get("strategy") in ("long_call", "long_put")
    ):
        opt = snapshot.get("options") or {}
        ivr_raw = opt.get("iv_rank")
        ivr_f: float | None
        try:
            ivr_f = float(ivr_raw) if ivr_raw is not None else None
        except (TypeError, ValueError):
            ivr_f = None
        if ivr_f is not None and ivr_f >= IV_RANK_CRUSH_FLOOR:
            sp = float(out.get("size_pct") or 0) * 0.5
            if sp < 1e-8:
                return _with_hold(out, "iv_rank_high: halved size_pct rounds to zero")
            out["size_pct"] = min(sp, MAX_POSITION_PCT)

    if out.get("action") == "BUY" and snapshot:
        pc = snapshot.get("portfolio_context") or {}
        smap = pc.get("sector_notional_pct") or {}
        if not isinstance(smap, dict):
            smap = {}
        add = float(out.get("size_pct") or 0)
        sec_raw = snapshot.get("symbol_sector")
        sec_label = (
            sec_raw.strip()
            if isinstance(sec_raw, str) and sec_raw.strip()
            else "Unknown"
        )

        if MAX_UNKNOWN_SECTOR_PCT > 0 and sec_label == "Unknown":
            unk_existing = float(smap.get("Unknown") or 0)
            if unk_existing + add > MAX_UNKNOWN_SECTOR_PCT + 1e-9:
                return _with_hold(
                    out,
                    (
                        f"Unknown sector cap: would reach ~{unk_existing + add:.2%} "
                        f"(max {MAX_UNKNOWN_SECTOR_PCT:.2%})"
                    ),
                )

        if MAX_SECTOR_NOTIONAL_PCT > 0 and sec_label != "Unknown":
            existing = float(smap.get(sec_label) or 0)
            if existing + add > MAX_SECTOR_NOTIONAL_PCT + 1e-9:
                return _with_hold(
                    out,
                    (
                        f"Sector cap: {sec_label} would reach ~{existing + add:.2%} of equity "
                        f"(max {MAX_SECTOR_NOTIONAL_PCT:.2%})"
                    ),
                )

    if out.get("action") == "BUY" and MAX_CONCURRENT_BUYS > 0:
        open_n = int(account.get("open_positions") or 0)
        if open_n >= MAX_CONCURRENT_BUYS:
            msg = (
                f"Max concurrent positions ({MAX_CONCURRENT_BUYS}) reached "
                f"(currently {open_n} open)"
            )
            if RISK_SOFT_POLICY:
                _warn(out, f"RISK_SOFT_POLICY: would HOLD — {msg}")
            else:
                return _with_hold(out, msg)

    cool_h = _effective_cooldown_hours(snapshot)

    conn = _db_connect(DB_PATH)
    try:
        _ensure_risk_state(conn)

        halt_raw = _risk_kv_get(conn, "halt_until_utc")
        if halt_raw and out.get("action") == "BUY":
            ht = _parse_ts(halt_raw)
            if ht is not None:
                if ht.tzinfo is None:
                    ht = ht.replace(tzinfo=timezone.utc)
                if datetime.now(timezone.utc) < ht:
                    conn.commit()
                    return _with_hold(
                        out,
                        "broker_halted_after_daily_loss_emergency",
                    )

        giveback_day = (_risk_kv_get(conn, "giveback_halt_date_et") or "").strip()
        if out.get("action") == "BUY" and giveback_day == today_et.isoformat():
            conn.commit()
            return _with_hold(out, "intraday_giveback_halt_new_buys")

        peak_raw = _risk_kv_get(conn, "equity_peak")
        try:
            peak = float(peak_raw) if peak_raw is not None else 0.0
        except ValueError:
            peak = 0.0
        new_peak = max(peak, equity)
        _risk_kv_set(conn, "equity_peak", str(new_peak))
        if new_peak > 0 and equity < new_peak * (1.0 - MAX_DRAWDOWN_FROM_PEAK_PCT):
            conn.commit()
            return _with_halt(
                out,
                (
                    f"Drawdown from peak equity exceeds "
                    f"{MAX_DRAWDOWN_FROM_PEAK_PCT:.0%} (peak {new_peak:.2f}, now {equity:.2f})"
                ),
            )

        try:
            alpaca_dt = int(account.get("daytrade_count") or 0)
        except (TypeError, ValueError):
            alpaca_dt = 0
        today_iso = date.today().isoformat()
        pdt_count_raw = _risk_kv_get(conn, "pdt_count") or "0"
        try:
            stored_pdt = int(pdt_count_raw)
        except ValueError:
            stored_pdt = 0
        if stored_pdt != alpaca_dt:
            log.info(
                "pdt_count_divergence sqlite=%s alpaca_daytrade_count=%s",
                stored_pdt,
                alpaca_dt,
            )

        if (
            PDT_DAYTRADE_CEILING > 0
            and equity < PDT_EQUITY_FLOOR
            and out.get("action") == "BUY"
            and alpaca_dt >= PDT_DAYTRADE_CEILING
        ):
            if RISK_SOFT_POLICY:
                _warn(
                    out,
                    (
                        "RISK_SOFT_POLICY: would HOLD — pdt_ceiling "
                        f"(alpaca daytrade_count={alpaca_dt} ≥ {PDT_DAYTRADE_CEILING}, "
                        f"equity {equity:.0f} < {PDT_EQUITY_FLOOR:.0f})"
                    ),
                )
            else:
                conn.commit()
                return _with_hold(
                    out,
                    (
                        f"pdt_ceiling: daytrade_count={alpaca_dt} "
                        f"(≥{PDT_DAYTRADE_CEILING}) with equity < {PDT_EQUITY_FLOOR:.0f}"
                    ),
                )

        # Minimum hold time for options SELL — before overnight rule so same-day
        # low-conviction exits surface "Options min hold" (not min_hold_overnight).
        if OPTIONS_MIN_HOLD_HOURS > 0 and out.get("action") == "SELL" and out.get("strategy") in ("long_call", "long_put"):
            last_buy = _last_journal_buy_utc(sym)
            if last_buy is not None:
                now_utc = datetime.now(timezone.utc)
                age_h = (now_utc - last_buy).total_seconds() / 3600
                u_pct = _options_unrealized_pct_for_decision(pos, out)
                profit_bypass = _profit_lock_bypasses_min_hold(u_pct)
                if profit_bypass:
                    _warn(
                        out,
                        (
                            f"PROFIT_LOCK_PCT bypass: unrealized_pct {u_pct:.2%} "
                            f"≥ {PROFIT_LOCK_PCT:.0%}"
                        ),
                    )
                elif age_h < OPTIONS_MIN_HOLD_HOURS:
                    msg = (
                        f"Options min hold: position opened {age_h:.1f}h ago, "
                        f"must hold ≥{OPTIONS_MIN_HOLD_HOURS:.1f}h before selling"
                    )
                    conn.commit()
                    return _with_hold(out, msg)

        rek = _risk_exit_kind(out)
        if (
            MIN_HOLD_OVERNIGHT
            and out.get("action") == "SELL"
            and rek not in RISK_EXIT_KIND_OVERNIGHT_BYPASS
        ):
            last_buy = _last_journal_buy_utc(sym)
            if last_buy is not None and _utc_to_et_date(last_buy) == today_et:
                strat = out.get("strategy") or ""
                u_ov = (
                    _options_unrealized_pct_for_decision(pos, out)
                    if strat in ("long_call", "long_put")
                    else None
                )
                # Same-session take-profit on options: align with OPTIONS_MIN_HOLD bypass.
                if strat in ("long_call", "long_put") and _profit_lock_bypasses_min_hold(u_ov):
                    pass
                else:
                    conn.commit()
                    return _with_hold(out, "min_hold_overnight")

        if cool_h > 0 and out.get("action") == "BUY":
            last_sell = _last_journal_sell_utc(sym)
            if last_sell is not None:
                now = datetime.now(timezone.utc)
                if last_sell.tzinfo is None:
                    last_sell = last_sell.replace(tzinfo=timezone.utc)
                if now - last_sell < timedelta(hours=cool_h):
                    msg = (
                        f"Symbol cooldown: last SELL within {cool_h:.1f}h "
                        f"(journal; base {SYMBOL_COOLDOWN_HOURS:.0f}h × regime)"
                    )
                    if RISK_SOFT_POLICY:
                        _warn(out, f"RISK_SOFT_POLICY: would HOLD — {msg}")
                    else:
                        conn.commit()
                        return _with_hold(out, msg)

        conn.commit()
    finally:
        conn.close()

    out.pop("risk_exit_kind", None)
    if not out.get("risk_warnings"):
        out.pop("risk_warnings", None)
    return out


def record_day_trade() -> None:
    """Increment persisted PDT-style counter after a successful order."""
    today = date.today().isoformat()
    conn = _db_connect(DB_PATH)
    try:
        _ensure_risk_state(conn)
        pdt_date = _risk_kv_get(conn, "pdt_date") or today
        pdt_count_raw = _risk_kv_get(conn, "pdt_count") or "0"
        try:
            pdt_count = int(pdt_count_raw)
        except ValueError:
            pdt_count = 0
        if pdt_date != today:
            pdt_date = today
            pdt_count = 0
        pdt_count += 1
        _risk_kv_set(conn, "pdt_date", pdt_date)
        _risk_kv_set(conn, "pdt_count", str(pdt_count))
        conn.commit()
    finally:
        conn.close()
