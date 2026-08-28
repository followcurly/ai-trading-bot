"""CNN equity Fear & Greed (no-auth). TTL-cached, used by the regime engine.

Snapshot contract:
- Global: snap["fear_greed"] = {"score": 47.5, "rating": "neutral",
                                "previous_close": ..., "previous_1_week": ...}
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import requests

log = logging.getLogger("trader.sentiment")

_FNG_URL = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"

_FNG_TTL = float(os.getenv("FEAR_GREED_TTL_SEC", "3600"))

_FNG_CACHE: tuple[float, dict[str, Any] | None] | None = None


def _now() -> float:
    return time.monotonic()


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def fetch_fear_greed() -> dict[str, Any] | None:
    """CNN equity Fear & Greed (0-100). Undocumented JSON endpoint; UA header required."""
    global _FNG_CACHE
    if _truthy("FEAR_GREED_DISABLE"):
        return None

    now = _now()
    if _FNG_CACHE and now - _FNG_CACHE[0] < _FNG_TTL:
        return _FNG_CACHE[1]

    timeout = float(os.getenv("FEAR_GREED_TIMEOUT_SEC", "10"))
    try:
        # CNN's edge requires browser-shaped headers (Origin/Referer) or returns an empty body.
        r = requests.get(
            _FNG_URL,
            timeout=timeout,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "application/json",
                "Origin": "https://www.cnn.com",
                "Referer": "https://www.cnn.com/",
            },
        )
        r.raise_for_status()
        body = r.json()
    except Exception:
        log.exception("fear_greed_fetch_failed")
        _FNG_CACHE = (now, None)
        return None

    fg = body.get("fear_and_greed") if isinstance(body, dict) else None
    if not isinstance(fg, dict) or fg.get("score") is None:
        _FNG_CACHE = (now, None)
        return None

    out = {
        "score": round(float(fg.get("score")), 2),
        "rating": fg.get("rating"),
        "previous_close": fg.get("previous_close"),
        "previous_1_week": fg.get("previous_1_week"),
        "previous_1_month": fg.get("previous_1_month"),
        "previous_1_year": fg.get("previous_1_year"),
    }
    _FNG_CACHE = (now, out)
    return out
