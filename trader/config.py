"""Infra config for the trading scaffold (no strategy knobs)."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

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

NEWS_RSS_URLS = [
    u.strip()
    for u in os.getenv("NEWS_RSS_URLS", "").split(",")
    if u.strip()
]

TRADE_LOG_HOST = os.getenv("TRADE_LOG_HOST", "127.0.0.1")
TRADE_LOG_PORT = int(os.getenv("TRADE_LOG_PORT", "8788"))
TRADE_LOG_VIEW_TOKEN = os.getenv("TRADE_LOG_VIEW_TOKEN", "").strip()

# v2 red-day buy & hold
try:
    RED_DAY_BUY_PCT = max(0.0, float(os.getenv("RED_DAY_BUY_PCT", "0.05")))
except ValueError:
    RED_DAY_BUY_PCT = 0.05
try:
    RED_DAY_MIN_NOTIONAL = max(1.0, float(os.getenv("RED_DAY_MIN_NOTIONAL", "10")))
except ValueError:
    RED_DAY_MIN_NOTIONAL = 10.0
# HH:MM America/New_York — comma-separated scan times (morning + afternoon by default).
# Legacy RED_DAY_SCAN_TIME_ET still accepted as a single-slot override when TIMES unset.
_raw_times = (os.getenv("RED_DAY_SCAN_TIMES_ET") or "").strip()
if not _raw_times:
    legacy = (os.getenv("RED_DAY_SCAN_TIME_ET") or "").strip()
    _raw_times = legacy if legacy else "10:30,15:30"
RED_DAY_SCAN_TIMES_ET = _raw_times
# Back-compat alias (first slot)
RED_DAY_SCAN_TIME_ET = RED_DAY_SCAN_TIMES_ET.split(",")[0].strip() or "10:30"
TRADING_PROFILE = os.getenv("TRADING_PROFILE", "red_day_v2").strip() or "red_day_v2"
