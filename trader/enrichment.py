"""Free snapshot enrichment: yfinance (earnings) + RSS headlines. TTL caches, best-effort."""

from __future__ import annotations

import os
import time
from typing import Any
from urllib.parse import urlparse

import feedparser
import requests
import yfinance as yf

from trader.config import NEWS_RSS_URLS

_EARNINGS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_EARNINGS_TTL = float(os.getenv("ENRICHMENT_EARNINGS_TTL_SEC", "21600"))  # 6h

_RSS_CACHE: tuple[float, dict[str, Any]] | None = None
_RSS_TTL = float(os.getenv("ENRICHMENT_RSS_TTL_SEC", "1800"))  # 30m

_YAHOO_RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline?s={sym}&region=US&lang=en-US"


def _now() -> float:
    return time.monotonic()


def _resolve_rss_urls() -> list[str]:
    """Use NEWS_RSS_URLS when set; otherwise derive Yahoo per-symbol from the live watchlist."""
    if NEWS_RSS_URLS:
        return list(NEWS_RSS_URLS)
    try:
        from trader.watchlist import get_watchlist

        syms = get_watchlist()
    except Exception:
        syms = []
    return [_YAHOO_RSS.format(sym=s) for s in syms[:20]]


def _earnings_for_symbol(symbol: str) -> dict[str, Any]:
    sym = symbol.strip().upper()
    now = _now()
    hit = _EARNINGS_CACHE.get(sym)
    if hit and now - hit[0] < _EARNINGS_TTL:
        return hit[1]

    out: dict[str, Any] = {
        "earnings_days_away": None,
        "next_earnings_date": None,
    }
    try:
        tk = yf.Ticker(sym)
        df = tk.get_earnings_dates(limit=12)
        if df is not None and not df.empty:
            from datetime import date, datetime, timezone

            today = date.today()
            for ts in sorted(df.index):
                d = ts.date() if hasattr(ts, "date") else ts
                if d >= today:
                    out["earnings_days_away"] = (d - today).days
                    out["next_earnings_date"] = str(d)
                    break
    except Exception:
        pass

    _EARNINGS_CACHE[sym] = (now, out)
    return out


def _fetch_rss_bundle() -> dict[str, Any]:
    global _RSS_CACHE
    now = _now()
    if _RSS_CACHE and now - _RSS_CACHE[0] < _RSS_TTL:
        return _RSS_CACHE[1]

    headlines: list[str] = []
    errors: list[str] = []
    max_titles = int(os.getenv("ENRICHMENT_RSS_MAX_TITLES", "24"))
    per_feed_cap = int(os.getenv("ENRICHMENT_RSS_PER_FEED", "8"))
    timeout = float(os.getenv("ENRICHMENT_RSS_TIMEOUT_SEC", "8"))

    for raw_url in _resolve_rss_urls():
        url = raw_url.strip()
        if not url:
            continue
        try:
            host = urlparse(url).netloc or url[:40]
            r = requests.get(url, timeout=timeout, headers={"User-Agent": "ai-trading-bot/1.0"})
            r.raise_for_status()
            feed = feedparser.parse(r.content)
            n = 0
            for ent in feed.entries:
                if n >= per_feed_cap:
                    break
                title = (ent.get("title") or "").strip()
                if title:
                    headlines.append(f"[{host}] {title}")
                    n += 1
                if len(headlines) >= max_titles:
                    break
        except Exception as e:
            errors.append(f"{url[:48]}: {e!s}")

    out = {
        "news_headlines": headlines[:max_titles],
        "news_feed_errors": errors or None,
    }
    _RSS_CACHE = (now, out)
    return out


def _headlines_for_symbol(symbol: str, all_headlines: list[str]) -> list[str]:
    sym = symbol.strip().upper()
    max_out = int(os.getenv("ENRICHMENT_RSS_MAX_PER_SYMBOL", "12"))
    max_chars = int(os.getenv("ENRICHMENT_MAX_HEADLINE_CHARS", "200"))
    matched = [h for h in all_headlines if sym and sym in h.upper()]
    out: list[str] = []
    for h in matched:
        if len(h) > max_chars:
            h = h[:max_chars] + "…"
        out.append(h)
        if len(out) >= max_out:
            break
    if not out and all_headlines:
        for h in all_headlines[:4]:
            if len(h) > max_chars:
                h = h[:max_chars] + "…"
            out.append(h)
    return out


def _cap_news_payload(headlines: list[str]) -> list[str]:
    max_bytes = int(os.getenv("ENRICHMENT_MAX_NEWS_BYTES", "8192"))
    out: list[str] = []
    used = 0
    for h in headlines:
        b = len(h.encode("utf-8")) + 1
        if used + b > max_bytes:
            break
        out.append(h)
        used += b
    return out


def enrich_snapshot(symbol: str, snapshot: dict) -> dict:
    """Mutates snapshot in place: earnings + news; keeps best-effort."""
    if os.getenv("ENRICHMENT_DISABLE", "").strip().lower() in ("1", "true", "yes", "on"):
        snapshot.setdefault("news_headlines", [])
        snapshot.setdefault("news_sentiment", "neutral")
        return snapshot

    e = _earnings_for_symbol(symbol)
    snapshot["earnings_days_away"] = e.get("earnings_days_away")
    snapshot["next_earnings_date"] = e.get("next_earnings_date")

    rss = _fetch_rss_bundle()
    raw_headlines = rss.get("news_headlines") or []
    filtered = _headlines_for_symbol(symbol, raw_headlines)
    snapshot["news_headlines"] = _cap_news_payload(filtered)
    if rss.get("news_feed_errors"):
        snapshot["news_feed_errors"] = rss["news_feed_errors"]

    if snapshot.get("news_headlines"):
        snapshot["news_sentiment"] = "see_headlines"
    return snapshot
