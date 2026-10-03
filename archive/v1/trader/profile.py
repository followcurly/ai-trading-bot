"""Trading profiles — bundled env presets for default vs aggressive YOLO options."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Literal

log = logging.getLogger("trader.profile")

ProfileName = Literal["default", "yolo_options"]
BrainVariant = Literal["default", "yolo_options"]

YOLO_WATCHLIST = (
    "TQQQ,SOXL,TSLL,SQQQ,UVIX,SPY,QQQ,NVDA,AMD,META"
)

_DEFAULT_STRATEGIES = (
    "long_stock",
    "long_call",
    "long_put",
    "csp",
    "vertical_spread",
)

_YOLO_STRATEGIES = ("long_call", "long_put")

# Presets applied only when the key is absent from os.environ (operator overrides win).
_YOLO_ENV_DEFAULTS: dict[str, str] = {
    "TRADING_WATCHLIST": YOLO_WATCHLIST,
    "WATCHLIST_SCREENER_SIZE": "0",
    "WATCHLIST_INCLUDE_OPEN_POSITIONS": "true",
    "WATCHLIST_MAX_SPREAD_PCT": "0.03",
    "MIN_OPTION_DTE": "1",
    "MAX_POSITION_PCT": "0.12",
    "OPTIONS_SIZE_TIERS": "0.65:3,0.75:5",
    "MAX_OPTIONS_CONTRACTS_HARD": "5",
    "MAX_CONCURRENT_BUYS": "3",
    "MIN_CONFIDENCE": "0.60",
    "IV_RANK_CRUSH_FLOOR": "0.95",
    "MAX_DAILY_LOSS_PCT": "0.05",
    "DAILY_LOSS_TRIGGER_EXIT_ALL": "false",
    "DAILY_GIVEBACK_HALT_PCT": "0.70",
    "EARNINGS_BLACKOUT_DAYS": "1",
    "OPTIONS_PROFIT_LADDER_PCT": "2.0,5.0,10.0",
    "OPTIONS_PROFIT_LADDER_FRACTION": "0.33,0.66,1.0",
    "OPTIONS_PROFIT_LADDER_DISABLE": "false",
    "TRAIL_GIVEBACK_PCT": "0.55",
    "OPTIONS_HARD_STOP_PCT": "-0.65",
    "OPTIONS_MIN_HOLD_HOURS": "0.25",
    "POSITION_HEALTH_INTERVAL_MIN": "2",
    "SCHEDULE_EXTRA_ENABLE": "true",
    # YOLO trims same-session losers; default profile keeps overnight-hold ON.
    # OPTIONS_MIN_HOLD_HOURS=0.25 still gates options exits within 15 min of entry,
    # so this only opens up SELLs on options held > 15 min, same day.
    "MIN_HOLD_OVERNIGHT": "false",
}


@dataclass(frozen=True)
class TradingProfile:
    name: ProfileName
    allowed_strategies: tuple[str, ...]
    brain_variant: BrainVariant
    static_watchlist_only: bool


_PROFILES: dict[ProfileName, TradingProfile] = {
    "default": TradingProfile(
        name="default",
        allowed_strategies=_DEFAULT_STRATEGIES,
        brain_variant="default",
        static_watchlist_only=False,
    ),
    "yolo_options": TradingProfile(
        name="yolo_options",
        allowed_strategies=_YOLO_STRATEGIES,
        brain_variant="yolo_options",
        static_watchlist_only=True,
    ),
}

_active: TradingProfile | None = None


def _resolve_name() -> ProfileName:
    raw = (os.getenv("TRADING_PROFILE") or "default").strip().lower()
    if raw in _PROFILES:
        return raw  # type: ignore[return-value]
    log.warning("unknown TRADING_PROFILE=%s; using default", raw)
    return "default"


def get_profile() -> TradingProfile:
    global _active
    if _active is None:
        _active = _PROFILES[_resolve_name()]
    return _active


def apply_env_defaults() -> ProfileName:
    """Set YOLO preset env vars only when not already defined by the operator."""
    name = _resolve_name()
    if name != "yolo_options":
        return name
    applied: list[str] = []
    for key, value in _YOLO_ENV_DEFAULTS.items():
        if key not in os.environ:
            os.environ[key] = value
            applied.append(key)
    if applied:
        log.info(
            "profile_env_defaults profile=yolo_options applied_count=%d",
            len(applied),
        )
    return name


def log_profile_startup(*, paper: bool) -> None:
    p = get_profile()
    log.info(
        "trading_profile=%s paper=%s brain_variant=%s strategies=%s",
        p.name,
        paper,
        p.brain_variant,
        ",".join(p.allowed_strategies),
    )


def effective_trail_min_track_pnl(entry_notional: float) -> float:
    """Per-position trail floor; YOLO uses max(static, 0.5% of entry notional)."""
    try:
        floor = float(os.getenv("TRAIL_MIN_TRACK_PNL", "25.0") or 25.0)
    except ValueError:
        floor = 25.0
    floor = max(0.0, floor)
    if get_profile().name != "yolo_options":
        return floor
    try:
        pct = float(os.getenv("TRAIL_MIN_TRACK_PCT_OF_NOTIONAL", "0.005") or 0.005)
    except ValueError:
        pct = 0.005
    return max(floor, max(0.0, entry_notional) * pct)


def default_fallback_strategy() -> str:
    return "long_call" if get_profile().name == "yolo_options" else "long_stock"


def check_live_profile_guard(*, paper: bool) -> None:
    """Require TRADING_PROFILE_LIVE_OK when running yolo_options on a live Alpaca host."""
    p = get_profile()
    if paper or p.name != "yolo_options":
        return
    ok = (os.getenv("TRADING_PROFILE_LIVE_OK") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if not ok:
        raise RuntimeError(
            "TRADING_PROFILE=yolo_options on a live Alpaca base URL requires "
            "TRADING_PROFILE_LIVE_OK=true (paper promotion gate)."
        )
