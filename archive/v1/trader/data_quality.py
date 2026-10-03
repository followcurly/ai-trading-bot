"""Snapshot completeness hints for the brain and journal (no network)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

_OPTIONAL_TOP_LEVEL_KEYS = (
    "macro",
    "vix",
    "fear_greed",
    "earnings_calendar",
    "analyst_recommendation",
    "options",
)


def build_data_quality(snapshot: dict[str, Any]) -> dict[str, Any]:
    missing_critical: list[str] = []
    price = snapshot.get("price")
    try:
        price_f = float(price) if price is not None else 0.0
    except (TypeError, ValueError):
        price_f = 0.0
    if price_f <= 0:
        missing_critical.append("price")

    ind = snapshot.get("indicators")
    if not isinstance(ind, dict):
        missing_critical.append("indicators")
    else:
        for k in ("rsi_14", "atr_14", "ema_20"):
            if ind.get(k) is None:
                missing_critical.append(f"indicators.{k}")
                break

    ac = snapshot.get("account")
    if not isinstance(ac, dict):
        missing_critical.append("account")
    else:
        try:
            if float(ac.get("equity", 0) or 0) <= 0:
                missing_critical.append("account.equity")
        except (TypeError, ValueError):
            missing_critical.append("account.equity")

    missing_optional = [k for k in _OPTIONAL_TOP_LEVEL_KEYS if not snapshot.get(k)]

    staleness_sec: float | None = None
    ts = snapshot.get("timestamp")
    if isinstance(ts, str) and ts.strip():
        try:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            staleness_sec = max(
                0.0, (datetime.now(timezone.utc) - t).total_seconds()
            )
        except ValueError:
            pass

    return {
        "missing_critical": missing_critical,
        "missing_optional": missing_optional,
        "staleness_sec": staleness_sec,
        "optional_keys_checked": list(_OPTIONAL_TOP_LEVEL_KEYS),
    }
