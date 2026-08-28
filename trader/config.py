import os
from pathlib import Path
from typing import NamedTuple

from dotenv import load_dotenv

load_dotenv()

from trader.profile import apply_env_defaults, get_profile, log_profile_startup

apply_env_defaults()

# Repo root when running as `python -m trader.main` from /srv/ai-trading-bot
REPO_ROOT = Path(__file__).resolve().parent.parent

LOG_DIR = Path(os.getenv("TRADING_LOG_DIR", str(REPO_ROOT / "data" / "logs")))
LOG_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = Path(os.getenv("TRADING_DB_PATH", str(LOG_DIR / "trades.db")))
JSONL_PATH = Path(os.getenv("TRADING_JSONL_PATH", str(LOG_DIR / "journal.jsonl")))


def _normalize_alpaca_trading_base(raw: str) -> str:
    """Alpaca-py builds paths as base + '/v2' + endpoint; strip accidental /v2 suffix."""
    u = raw.strip().rstrip("/")
    while u.lower().endswith("/v2"):
        u = u[:-3].rstrip("/")
    return u


_alpaca_raw = os.getenv("ALPACA_BASE_URL", "").strip()
ALPACA_BASE_URL_NORMALIZED = (
    _normalize_alpaca_trading_base(_alpaca_raw) if _alpaca_raw else ""
)
# Default to paper when unset (paper keys + paper API).
if not ALPACA_BASE_URL_NORMALIZED:
    ALPACA_PAPER = True
else:
    ALPACA_PAPER = ALPACA_BASE_URL_NORMALIZED.startswith("https://paper")

TRADING_PROFILE = get_profile().name
log_profile_startup(paper=ALPACA_PAPER)


def _env_bool(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


ANTHROPIC_MODEL_HAIKU = os.getenv("ANTHROPIC_MODEL_HAIKU", "claude-haiku-4-5-20251001")
# Cost kill-switch: force every "Sonnet" caller (analyst persona, weekly review,
# human blog) onto Haiku without touching call sites. Explicit ANTHROPIC_MODEL_SONNET
# in env still wins, so you can pin a specific model when re-enabling Sonnet later.
ANTHROPIC_FORCE_HAIKU = _env_bool("ANTHROPIC_FORCE_HAIKU", "false")
_DEFAULT_SONNET = ANTHROPIC_MODEL_HAIKU if ANTHROPIC_FORCE_HAIKU else "claude-sonnet-4-6"
ANTHROPIC_MODEL_SONNET = os.getenv("ANTHROPIC_MODEL_SONNET", _DEFAULT_SONNET)


BRAIN_TRADER_MODEL = os.getenv("BRAIN_TRADER_MODEL", ANTHROPIC_MODEL_HAIKU)


def _csv_upper_set(name: str, default: str) -> set[str]:
    raw = (os.getenv(name) or default).strip()
    return {s.strip().upper() for s in raw.split(",") if s.strip()}


try:
    REGIME_TTL_SEC = max(60, int(os.getenv("REGIME_TTL_SEC", "900")))
except ValueError:
    REGIME_TTL_SEC = 900

try:
    REGIME_BEARISH_INJECT_THRESHOLD = float(
        os.getenv("REGIME_BEARISH_INJECT_THRESHOLD", "0.55")
    )
except ValueError:
    REGIME_BEARISH_INJECT_THRESHOLD = 0.55

REGIME_BEARISH_VEHICLES_ALLOWLIST = _csv_upper_set(
    "REGIME_BEARISH_VEHICLES", "SQQQ,UVIX,SPXS,SDOW"
)
REGIME_DISABLE = _env_bool("REGIME_DISABLE", "false")

try:
    REGIME_MAX_TOKENS = max(200, int(os.getenv("REGIME_MAX_TOKENS", "400")))
except ValueError:
    REGIME_MAX_TOKENS = 400


# ---------------------------------------------------------------------------
# Position-health exit triggers
# ---------------------------------------------------------------------------
# Multi-day trailing peak: when False (default since 2026-05-13), `peak_unrealized_pnl`
# is the all-time high of the position, NOT just today's intraday high. This fixes the
# "DGXX problem" where yesterday's +$231 peak was thrown away every morning at the open.
# Set TRAIL_RESET_DAILY=true to restore the legacy intraday-only behavior.
TRAIL_RESET_DAILY = _env_bool("TRAIL_RESET_DAILY", "false")


def _csv_floats(name: str, default: str) -> list[float]:
    raw = (os.getenv(name) or default).strip()
    out: list[float] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(float(part))
        except ValueError:
            continue
    return out


# Profit ladder for option positions. Defaults tuned for base-hit swing trading
# (was lottery-ticket 2.0,5.0 / 0.5,1.0 before 2026-05-13):
#   rung 0 — close 67% of initial contracts at +20% unrealized return
#   rung 1 — close 100% (everything left) at +50% unrealized return
# For 1-contract positions, ceil(0.67 * 1) = 1, so the +20% rung fully exits.
# Set OPTIONS_PROFIT_LADDER_DISABLE=true to turn off; or override via env.
# Lengths must match.
OPTIONS_PROFIT_LADDER_DISABLE = _env_bool("OPTIONS_PROFIT_LADDER_DISABLE", "false")
OPTIONS_PROFIT_LADDER_PCT = _csv_floats("OPTIONS_PROFIT_LADDER_PCT", "0.20,0.50")
OPTIONS_PROFIT_LADDER_FRACTION = _csv_floats(
    "OPTIONS_PROFIT_LADDER_FRACTION", "0.67,1.0"
)
if len(OPTIONS_PROFIT_LADDER_PCT) != len(OPTIONS_PROFIT_LADDER_FRACTION):
    # Mismatch: disable rather than crash on boot. Operator should fix and restart.
    OPTIONS_PROFIT_LADDER_PCT = []
    OPTIONS_PROFIT_LADDER_FRACTION = []


# T-0 expiry sweep: auto-close any option position on its expiration date after a
# cutoff time (default 15:00 ET = 1 hour before close), to avoid forced
# auto-exercise / assignment. Set OPTIONS_EXPIRY_SWEEP_DISABLE=true to disable.
OPTIONS_EXPIRY_SWEEP_DISABLE = _env_bool("OPTIONS_EXPIRY_SWEEP_DISABLE", "false")
OPTIONS_EXPIRY_SWEEP_TIME_ET = (
    os.getenv("OPTIONS_EXPIRY_SWEEP_TIME_ET", "15:00").strip() or "15:00"
)


# Force-inject the underlyings of every open position into each cycle's watchlist
# so Claude can SELL or HOLD them even when they fall off the most-actives screener.
# This fixes the "1-day blindness on what we own" gap. Default ON.
WATCHLIST_INCLUDE_OPEN_POSITIONS = _env_bool(
    "WATCHLIST_INCLUDE_OPEN_POSITIONS", "true"
)


def _watchlist_screener_size() -> int:
    """How many screener symbols (after quality filters) before appending held underlyings.

    ``WATCHLIST_SCREENER_SIZE`` wins when set. Otherwise falls back to ``WATCHLIST_SIZE``
    (legacy alias). Default 20 (was 15) for lean asymmetric hunting without crowding out
    open positions.
    """
    raw = (os.getenv("WATCHLIST_SCREENER_SIZE") or "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    try:
        return max(1, int(os.getenv("WATCHLIST_SIZE", "20")))
    except ValueError:
        return 20


WATCHLIST_SCREENER_SIZE = _watchlist_screener_size()


class _OptionSizeTier(NamedTuple):
    threshold: float
    max_contracts: int


def _parse_options_size_tiers() -> list[_OptionSizeTier]:
    """Parse ``OPTIONS_SIZE_TIERS`` like ``0.70:2,0.78:3``. Ascending by threshold.

    Explicit empty env → [] (legacy flat cap: floor only). Malformed token → [].
    """
    if "OPTIONS_SIZE_TIERS" in os.environ and not (os.environ.get("OPTIONS_SIZE_TIERS") or "").strip():
        return []
    raw = (os.getenv("OPTIONS_SIZE_TIERS") or "0.70:2,0.78:3").strip()
    if not raw:
        return []
    tmp: list[_OptionSizeTier] = []
    for part in raw.split(","):
        part = part.strip()
        if not part or ":" not in part:
            return []
        left, right = part.split(":", 1)
        try:
            th = float(left.strip())
            mc = int(right.strip())
        except ValueError:
            return []
        if not (0.0 <= th <= 1.0) or mc < 1:
            return []
        tmp.append(_OptionSizeTier(th, mc))
    by_th: dict[float, int] = {}
    for t in tmp:
        by_th[t.threshold] = max(by_th.get(t.threshold, 0), t.max_contracts)
    return [_OptionSizeTier(k, v) for k, v in sorted(by_th.items())]


OPTIONS_SIZE_TIERS: list[_OptionSizeTier] = _parse_options_size_tiers()

try:
    MAX_OPTIONS_CONTRACTS_FLOOR = max(1, int(os.getenv("MAX_OPTIONS_CONTRACTS", "1")))
except ValueError:
    MAX_OPTIONS_CONTRACTS_FLOOR = 1

try:
    MAX_OPTIONS_CONTRACTS_HARD = max(1, int(os.getenv("MAX_OPTIONS_CONTRACTS_HARD", "3")))
except ValueError:
    MAX_OPTIONS_CONTRACTS_HARD = 3

# Floor must not exceed hard cap (misconfig safety).
if MAX_OPTIONS_CONTRACTS_FLOOR > MAX_OPTIONS_CONTRACTS_HARD:
    MAX_OPTIONS_CONTRACTS_FLOOR = MAX_OPTIONS_CONTRACTS_HARD

# Watchlist is dynamic — see trader/watchlist.py. TRADING_WATCHLIST kept only as an
# emergency static override (CSV, e.g. "SPY,QQQ"); empty = use the screener.
WATCHLIST_STATIC_OVERRIDE = [
    s.strip().upper()
    for s in os.getenv("TRADING_WATCHLIST", "").split(",")
    if s.strip()
]

# Optional static RSS override. EMPTY (default) -> trader/enrichment.py derives
# Yahoo per-symbol feeds from the live dynamic watchlist.
NEWS_RSS_URLS = [
    u.strip()
    for u in os.getenv("NEWS_RSS_URLS", "").split(",")
    if u.strip()
]

TRADE_LOG_HOST = os.getenv("TRADE_LOG_HOST", "127.0.0.1")
TRADE_LOG_PORT = int(os.getenv("TRADE_LOG_PORT", "8788"))
TRADE_LOG_VIEW_TOKEN = os.getenv("TRADE_LOG_VIEW_TOKEN", "").strip()
