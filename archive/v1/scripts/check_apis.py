#!/usr/bin/env python3
"""Probe external APIs and print enrichment-layer availability. Run:

    cd /srv/ai-trading-bot && .venv/bin/python scripts/check_apis.py

Loads `.env` then `/root/.openclaw/api.env` when present. Prints OK / SKIP / FAIL per
probe, then a snapshot-oriented summary. Read-only, safe in prod.

**yfinance earnings** needs optional `lxml` (listed in `requirements.txt`) for
`get_earnings_dates`. Override probe symbol with `ENRICHMENT_EARNINGS_PROBE_SYMBOL`
(default `AAPL`; ETFs like SPY often have no earnings table).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Callable

# Allow `python scripts/check_apis.py` from anywhere — make `trader` importable.
_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from dotenv import load_dotenv

# Load secrets like the live bot: repo .env then the shared api.env (later wins for dup keys).
_ENV_CANDIDATES = [
    _REPO / ".env",
    Path("/root/.openclaw/api.env"),
    Path.home() / ".openclaw" / "api.env",
]
for p in _ENV_CANDIDATES:
    if p.is_file():
        load_dotenv(p, override=False)
load_dotenv(override=False)

GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"


def _print(label: str, status: str, detail: str = "") -> None:
    color = {"OK": GREEN, "SKIP": YELLOW, "FAIL": RED}.get(status, "")
    print(f"  {color}{status:<4}{RESET}  {label:<24}  {detail}")


def _timed(fn: Callable[[], tuple[str, str]]) -> tuple[str, str, float]:
    t0 = time.monotonic()
    try:
        status, detail = fn()
    except Exception as e:
        status, detail = "FAIL", repr(e)[:160]
    return status, detail, (time.monotonic() - t0) * 1000


def check_anthropic_haiku() -> tuple[str, str]:
    key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        return "SKIP", "ANTHROPIC_API_KEY not set"
    import anthropic

    client = anthropic.Anthropic(api_key=key)
    model = os.getenv("ANTHROPIC_MODEL_HAIKU", "claude-haiku-4-5-20251001")
    msg = client.messages.create(
        model=model,
        max_tokens=4,
        messages=[{"role": "user", "content": "ping"}],
    )
    return "OK", f"model={msg.model} stop={msg.stop_reason}"


def check_anthropic_sonnet() -> tuple[str, str]:
    key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        return "SKIP", "ANTHROPIC_API_KEY not set"
    import anthropic

    # Weekly review / blog still use Sonnet when configured (see ANTHROPIC_MODEL_SONNET).
    from trader.config import ANTHROPIC_MODEL_SONNET

    client = anthropic.Anthropic(api_key=key)
    msg = client.messages.create(
        model=ANTHROPIC_MODEL_SONNET,
        max_tokens=4,
        messages=[{"role": "user", "content": "ping"}],
    )
    return "OK", f"model={msg.model} stop={msg.stop_reason}"


def check_alpaca_trading() -> tuple[str, str]:
    if not (os.getenv("ALPACA_API_KEY") and os.getenv("ALPACA_SECRET_KEY")):
        return "SKIP", "ALPACA_API_KEY / ALPACA_SECRET_KEY not set"
    from trader.alpaca_runtime import trading_client

    acc = trading_client().get_account()
    return "OK", f"acct={acc.account_number} equity=${float(acc.equity):,.2f} status={acc.status}"


def check_alpaca_data() -> tuple[str, str]:
    if not (os.getenv("ALPACA_API_KEY") and os.getenv("ALPACA_SECRET_KEY")):
        return "SKIP", "Alpaca keys not set"
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockLatestTradeRequest

    from trader.alpaca_runtime import data_client

    resp = data_client().get_stock_latest_trade(
        StockLatestTradeRequest(symbol_or_symbols=["SPY"], feed=DataFeed.IEX)
    )
    spy = resp["SPY"]
    return "OK", f"SPY last={spy.price} ts={spy.timestamp.isoformat()}"


def check_alpaca_screener() -> tuple[str, str]:
    if not (os.getenv("ALPACA_API_KEY") and os.getenv("ALPACA_SECRET_KEY")):
        return "SKIP", "Alpaca keys not set"
    from alpaca.data.requests import MostActivesRequest

    from trader.alpaca_runtime import screener_client

    r = screener_client().get_most_actives(MostActivesRequest(top=5, by="volume"))
    items = getattr(r, "most_actives", []) or []
    syms = [getattr(it, "symbol", "?") for it in items]
    return "OK", f"top5_actives={syms}"


def check_alpaca_options() -> tuple[str, str]:
    if not (os.getenv("ALPACA_API_KEY") and os.getenv("ALPACA_SECRET_KEY")):
        return "SKIP", "Alpaca keys not set"
    from trader.options_exec import option_atm_context

    ctx = option_atm_context("SPY", 580.0)
    if not ctx:
        return "FAIL", "no ATM context returned for SPY"
    return "OK", f"expiry={ctx.get('expiry')} atm={ctx.get('atm_strike')} ring={len(ctx.get('strikes_ring') or {})}"


def check_yfinance_vix() -> tuple[str, str]:
    from trader.macro import _vix_bundle

    v = _vix_bundle()
    if not v:
        return "FAIL", "yfinance returned no VIX history"
    return "OK", f"VIX level={v['level']} regime={v['regime']} chg%={v['change_pct']:+.4f}"


def check_yahoo_rss() -> tuple[str, str]:
    import requests

    r = requests.get(
        "https://feeds.finance.yahoo.com/rss/2.0/headline?s=SPY&region=US&lang=en-US",
        timeout=10,
        headers={"User-Agent": "ai-trading-bot/1.0"},
    )
    r.raise_for_status()
    import feedparser

    feed = feedparser.parse(r.content)
    return "OK", f"entries={len(feed.entries)} status={r.status_code}"


def check_fred() -> tuple[str, str]:
    key = (os.getenv("FRED_API_KEY") or "").strip()
    if not key:
        return "SKIP", "FRED_API_KEY not set (macro will be omitted)"
    from trader.macro import _fred_latest

    val = _fred_latest(key, "DGS10")
    if val is None:
        return "FAIL", "no DGS10 observation"
    return "OK", f"DGS10={val}"


def check_fear_greed() -> tuple[str, str]:
    from trader.sentiment import fetch_fear_greed

    fg = fetch_fear_greed()
    if not fg:
        return "FAIL", "no body returned (CNN edge?)"
    return "OK", f"score={fg['score']} rating={fg.get('rating')}"


def check_finnhub() -> tuple[str, str]:
    if not (os.getenv("FINNHUB_API_KEY") or "").strip():
        return "SKIP", "FINNHUB_API_KEY not set"
    from trader.finnhub import fetch_earnings_calendar, fetch_recommendation

    cal = fetch_earnings_calendar() or []
    rec = fetch_recommendation("AAPL")
    return "OK", f"calendar_rows={len(cal)} aapl_rec={rec}"


def check_yfinance_sector() -> tuple[str, str]:
    """Same path as trader/sector.py (GICS-style label for risk + snapshot)."""
    from trader.sector import clear_sector_cache, get_sector

    clear_sector_cache()
    s = get_sector("AAPL")
    if not s:
        return "FAIL", "no sector label for AAPL (yfinance .info)"
    return "OK", f"AAPL sector={s!r}"


def check_yfinance_earnings() -> tuple[str, str]:
    """Same path as enrichment earnings (yfinance). Use a common equity — ETFs often have no dates."""
    import yfinance as yf

    sym = (os.getenv("ENRICHMENT_EARNINGS_PROBE_SYMBOL") or "AAPL").strip().upper() or "AAPL"
    tk = yf.Ticker(sym)
    df = tk.get_earnings_dates(limit=3)
    if df is None or df.empty:
        return "FAIL", f"no earnings_dates for {sym}"
    return "OK", f"{sym} rows={len(df)}"


def check_healthcheck() -> tuple[str, str]:
    url = (os.getenv("HEALTHCHECK_URL") or "").strip()
    if not url:
        return "SKIP", "HEALTHCHECK_URL not set"
    import requests

    r = requests.get(url, timeout=5)
    return ("OK" if r.ok else "FAIL"), f"status={r.status_code}"


CHECKS = [
    ("Anthropic Haiku (trader)", check_anthropic_haiku),
    ("Anthropic Sonnet (analyst)", check_anthropic_sonnet),
    ("Alpaca Trading", check_alpaca_trading),
    ("Alpaca Market Data", check_alpaca_data),
    ("Alpaca Screener", check_alpaca_screener),
    ("Alpaca Options ATM", check_alpaca_options),
    ("yfinance (VIX)", check_yfinance_vix),
    ("yfinance sector", check_yfinance_sector),
    ("Yahoo Finance RSS", check_yahoo_rss),
    ("FRED (macro)", check_fred),
    ("CNN Fear & Greed", check_fear_greed),
    ("Finnhub", check_finnhub),
    ("yfinance earnings (enrich)", check_yfinance_earnings),
    ("Healthchecks.io", check_healthcheck),
]

# Snapshot key -> depends on which check label(s); used for summary only.
_ENRICH_LAYERS: list[tuple[str, str, tuple[str, ...]]] = [
    ("options", "Alpaca option chain / ATM context", ("Alpaca Options ATM",)),
    ("macro", "FRED yields / CPI / unrate", ("FRED (macro)",)),
    ("vix", "VIX level + regime", ("yfinance (VIX)",)),
    ("fear_greed", "CNN Fear & Greed", ("CNN Fear & Greed",)),
    ("earnings_calendar", "Finnhub calendar", ("Finnhub",)),
    ("analyst_recommendation", "Finnhub recs", ("Finnhub",)),
    ("news_headlines (RSS)", "Yahoo RSS bundle in enrich_snapshot", ("Yahoo Finance RSS",)),
    ("earnings (yfinance)", "Per-symbol earnings_days_away in enrich_snapshot", ("yfinance earnings (enrich)",)),
    (
        "symbol_sector (yfinance)",
        "Sector labels in snapshot / MAX_SECTOR_NOTIONAL_PCT",
        ("yfinance sector",),
    ),
]


def main() -> int:
    print("\nAPI connection check\n")
    results: dict[str, str] = {}
    fails = 0
    for label, fn in CHECKS:
        status, detail, ms = _timed(fn)
        results[label] = status
        if status == "FAIL":
            fails += 1
        _print(label, status, f"({ms:>5.0f}ms)  {detail}")

    print("\n--- Enrichment layer availability (for Claude snapshot) ---\n")
    for snap_key, description, dep_labels in _ENRICH_LAYERS:
        sts = [results.get(l, "?") for l in dep_labels]
        if any(s == "FAIL" for s in sts):
            avail = "UNAVAILABLE"
            reason = "at least one dependency FAILED"
        elif all(s == "SKIP" for s in sts):
            avail = "NOT CONFIGURED"
            reason = "API key(s) or env not set"
        elif any(s == "OK" for s in sts):
            if all(s in ("OK", "SKIP") for s in sts) and any(s == "OK" for s in sts):
                avail = "AVAILABLE"
                reason = "OK for required probe(s)"
            else:
                avail = "PARTIAL"
                reason = ",".join(f"{l}={results.get(l)}" for l in dep_labels)
        else:
            avail = "UNKNOWN"
            reason = ",".join(f"{l}={results.get(l)}" for l in dep_labels)
        col = GREEN if avail == "AVAILABLE" else (YELLOW if avail in ("NOT CONFIGURED", "PARTIAL") else RED)
        print(f"  {col}{avail:<18}{RESET}  {snap_key:<28}  {description}")
        if avail != "AVAILABLE":
            print(f"           └─ {reason}  [{', '.join(dep_labels)}]")

    # Whole-pipeline toggles
    print("\n--- Whole-block env toggles (if set, layers are skipped regardless of keys) ---\n")
    toggles = [
        ("MACRO_DISABLE", "Skips macro + vix in enrich_macro"),
        ("ENRICHMENT_DISABLE", "Skips RSS + yfinance earnings in enrich_snapshot"),
        ("OPTIONS_SNAPSHOT_DISABLE", "Skips option ATM context"),
        ("SECTOR_LOOKUP_DISABLE", "Skips yfinance sector labels"),
        ("FEAR_GREED_DISABLE", "Skips Fear & Greed"),
        ("FINNHUB_DISABLE", "Skips Finnhub"),
    ]
    for name, note in toggles:
        raw = (os.getenv(name) or "").strip().lower()
        on = raw in ("1", "true", "yes", "on")
        col = YELLOW if on else RESET
        print(f"  {col}{name}={'ON' if on else 'off'}{RESET}  — {note}")

    print()
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
