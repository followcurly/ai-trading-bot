"""Once-per-cycle cross-asset regime verdict (Haiku) + TTL cache.

Feeds macro/VIX/Fear&Greed + SPY/QQQ indicator slices into a compact payload.
Result is threaded into the watchlist (bearish vehicle injection) and each
per-symbol snapshot as ``market_regime``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any

import anthropic

from trader.claude_brain import _messages_create, _parse_json_response, _system_cached
from trader.config import (
    BRAIN_TRADER_MODEL,
    REGIME_BEARISH_INJECT_THRESHOLD,
    REGIME_BEARISH_VEHICLES_ALLOWLIST,
    REGIME_DISABLE,
    REGIME_MAX_TOKENS,
    REGIME_TTL_SEC,
)
from trader.data_feed import get_market_snapshot

log = logging.getLogger("trader.regime")

_MARKET_REGIME_CACHE: tuple[float, MarketRegime] | None = None


@dataclass
class MarketRegime:
    direction: str  # bullish | bearish | neutral
    conviction: float  # 0.0–1.0
    bearish_vehicles: list[str] = field(default_factory=list)
    rationale: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    def cache_key(self) -> str:
        v = ",".join(self.bearish_vehicles)
        return f"{self.direction}|{self.conviction:.4f}|{v}"


_REGIME_SYSTEM = """You are a macro regime analyst for US equities.

Given cross-asset JSON signals, reply with ONE JSON object only (no markdown, no prose):
{
  "direction": "bullish" | "bearish" | "neutral",
  "conviction": <number 0 to 1>,
  "bearish_vehicles": [<up to 3 inverse or volatility ETF tickers from the allowed list only>],
  "rationale": "<one short sentence>"
}

Rules:
- Use VIX level/regime, yield curve / macro if present, Fear & Greed, and SPY/QQQ
  momentum (RSI, MACD, ADX) plus the early-warning leading signals provided.
- ``bearish_vehicles`` MUST be empty unless direction is "bearish"; then pick from the
  allowed list in the user payload only.
- conviction reflects how decisive the evidence is; use low values when mixed or data sparse.

## Early-session bear-day detection (lagging indicators lie at the open)
RSI, MACD, and ADX are calculated from prior bars and reflect YESTERDAY'S trend, not
today's macro shock. Weight the following LEADING signals more heavily at session open:

- **VIX rising:** if ``vix_change_pct > 0.02`` (VIX up >2% today), institutional
  hedging is accelerating — treat as a soft bearish tilt regardless of VIX level.
  If ``vix_change_pct > 0.05``, treat as a strong bearish tilt.
- **Index gap-down:** if ``spy.change_pct < -0.003`` AND ``qqq.change_pct < -0.003``
  (both down more than 0.3% intraday), that is risk-off price action — lean bearish.
  If both are down >0.8%, lean strongly bearish even if RSI/MACD look fine.
- **VIX rising + gap-down together:** when BOTH conditions above are true simultaneously,
  call "bearish" unless macro/kalshi strongly contradicts.
- **Fear & Greed trend:** if ``fear_greed.score < fear_greed.previous_1_week``, sentiment
  is deteriorating — soft bearish tilt. Rapid drops (>5 points week-over-week) add
  meaningful conviction.
- **Lagging indicators:** high RSI or positive MACD from yesterday's close does NOT
  override the above early signals. A market can have RSI 65 from last week's rally and
  still gap down hard today on macro news.
"""


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def _bench_slice(snap: dict[str, Any]) -> dict[str, Any]:
    ind = snap.get("indicators") or {}
    return {
        "price": snap.get("price"),
        "rsi_14": ind.get("rsi_14"),
        "macd": ind.get("macd"),
        "macd_signal": ind.get("macd_signal"),
        "adx_14": ind.get("adx_14"),
        "obv": ind.get("obv"),
        "bb_upper": ind.get("bb_upper"),
        "bb_lower": ind.get("bb_lower"),
        "change_pct": ind.get("change_pct"),
    }


def _neutral_fallback(reason: str) -> MarketRegime:
    return MarketRegime(
        direction="neutral",
        conviction=0.0,
        bearish_vehicles=[],
        rationale=reason,
        raw={"fallback": True, "reason": reason},
    )


def _normalize_direction(raw: str) -> str:
    d = (raw or "").strip().lower()
    if d in ("bull", "bullish", "long"):
        return "bullish"
    if d in ("bear", "bearish", "short"):
        return "bearish"
    return "neutral"


def _clamp01(x: Any) -> float:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, v))


def _filter_vehicles(tickers: Any, allow: set[str]) -> list[str]:
    out: list[str] = []
    if not isinstance(tickers, list):
        return out
    for t in tickers:
        sym = str(t or "").strip().upper()
        if sym and sym in allow and sym not in out:
            out.append(sym)
        if len(out) >= 3:
            break
    return out


def _extract_leading_signals(spy: dict[str, Any], qqq: dict[str, Any]) -> dict[str, Any]:
    """Promote the most time-sensitive bear-day signals to top-level keys.

    These are surfaced separately from ``spy``/``qqq`` slices so the regime
    model sees them first and does not bury them under lagging RSI/MACD data.
    """
    vix = spy.get("vix") or {}
    try:
        vix_change_pct = float(vix.get("change_pct") or 0)
    except (TypeError, ValueError):
        vix_change_pct = 0.0

    spy_ind = spy.get("indicators") or {}
    qqq_ind = qqq.get("indicators") or {}
    try:
        spy_change = float(spy_ind.get("change_pct") or 0)
    except (TypeError, ValueError):
        spy_change = 0.0
    try:
        qqq_change = float(qqq_ind.get("change_pct") or 0)
    except (TypeError, ValueError):
        qqq_change = 0.0

    fg = spy.get("fear_greed") or {}
    try:
        fg_score = float(fg.get("score") or 0)
        fg_prev_week = float(fg.get("previous_1_week") or fg_score)
        fg_week_delta = round(fg_score - fg_prev_week, 2)
    except (TypeError, ValueError):
        fg_score = fg_prev_week = fg_week_delta = 0.0

    # Synthesise a plain-English early-warning note so the model doesn't miss it.
    notes: list[str] = []
    if vix_change_pct > 0.05:
        notes.append(f"VIX surging +{vix_change_pct:.1%} today — strong institutional hedging")
    elif vix_change_pct > 0.02:
        notes.append(f"VIX rising +{vix_change_pct:.1%} today — soft risk-off signal")
    if spy_change < -0.008 and qqq_change < -0.008:
        notes.append(f"Both SPY ({spy_change:.2%}) and QQQ ({qqq_change:.2%}) down sharply — macro risk-off")
    elif spy_change < -0.003 and qqq_change < -0.003:
        notes.append(f"SPY ({spy_change:.2%}) and QQQ ({qqq_change:.2%}) both negative — mild gap-down")
    if fg_week_delta < -5:
        notes.append(f"Fear & Greed dropped {fg_week_delta:.1f} pts week-over-week — deteriorating sentiment")

    return {
        "vix_change_pct": round(vix_change_pct, 4),
        "spy_change_pct": round(spy_change, 4),
        "qqq_change_pct": round(qqq_change, 4),
        "fear_greed_score": round(fg_score, 2),
        "fear_greed_week_delta": round(fg_week_delta, 2),
        "early_warning_notes": notes,
    }


def _build_regime_payload() -> dict[str, Any]:
    """Cross-asset context only (no per-cycle symbol bias)."""
    spy = get_market_snapshot("SPY")
    qqq = get_market_snapshot("QQQ")
    leading = _extract_leading_signals(spy, qqq)
    payload: dict[str, Any] = {
        "allowed_bearish_vehicles": sorted(REGIME_BEARISH_VEHICLES_ALLOWLIST),
        "leading_signals": leading,
        "spy": _bench_slice(spy),
        "qqq": _bench_slice(qqq),
        "vix": spy.get("vix"),
        "macro": spy.get("macro"),
        "fear_greed": spy.get("fear_greed"),
    }
    return payload


def get_market_regime() -> MarketRegime:
    """Return cached or fresh regime verdict (one Haiku call per TTL window)."""
    global _MARKET_REGIME_CACHE

    if REGIME_DISABLE:
        return _neutral_fallback("regime_disabled")

    now = time.monotonic()
    if _MARKET_REGIME_CACHE and now - _MARKET_REGIME_CACHE[0] < REGIME_TTL_SEC:
        hit = _MARKET_REGIME_CACHE[1]
        return MarketRegime(
            direction=hit.direction,
            conviction=hit.conviction,
            bearish_vehicles=list(hit.bearish_vehicles),
            rationale=hit.rationale,
            raw=dict(hit.raw),
        )

    if not (os.getenv("ANTHROPIC_API_KEY") or "").strip():
        regime = _neutral_fallback("no_anthropic_key")
        _MARKET_REGIME_CACHE = (now, regime)
        return regime

    allow = REGIME_BEARISH_VEHICLES_ALLOWLIST
    try:
        bundle = _build_regime_payload()
    except Exception as e:
        log.warning("regime_payload_build_failed err=%s", e)
        regime = _neutral_fallback("payload_build_failed")
        _MARKET_REGIME_CACHE = (now, regime)
        return regime

    user_blob = json.dumps(bundle, default=str)
    user_content = (
        "CROSS_ASSET_SIGNALS_JSON:\n"
        + user_blob
        + "\n\nReturn the regime JSON object described in your instructions."
    )

    try:
        text = _messages_create(
            _client(),
            BRAIN_TRADER_MODEL,
            _system_cached(_REGIME_SYSTEM),
            user_content,
            max_tokens=REGIME_MAX_TOKENS,
        )
        raw = _parse_json_response(text)
    except Exception as e:
        log.warning("regime_model_failed err=%s", e)
        regime = _neutral_fallback("model_or_parse_failed")
        _MARKET_REGIME_CACHE = (now, regime)
        return regime

    direction = _normalize_direction(str(raw.get("direction", "neutral")))
    conviction = _clamp01(raw.get("conviction"))
    rationale = str(raw.get("rationale") or "").strip() or "model_output"
    vehicles = _filter_vehicles(raw.get("bearish_vehicles"), allow)
    if direction != "bearish":
        vehicles = []

    regime = MarketRegime(
        direction=direction,
        conviction=conviction,
        bearish_vehicles=vehicles,
        rationale=rationale[:500],
        raw=dict(raw),
    )
    _MARKET_REGIME_CACHE = (now, regime)
    log.info(
        "market_regime direction=%s conviction=%.2f vehicles=%s",
        regime.direction,
        regime.conviction,
        regime.bearish_vehicles,
    )
    return MarketRegime(
        direction=regime.direction,
        conviction=regime.conviction,
        bearish_vehicles=list(regime.bearish_vehicles),
        rationale=regime.rationale,
        raw=dict(regime.raw),
    )


def regime_bearish_inject_symbols(regime: MarketRegime | None) -> list[str]:
    """Symbols to merge into the watchlist when regime is bearish enough."""
    if regime is None:
        return []
    if regime.direction != "bearish":
        return []
    if regime.conviction < REGIME_BEARISH_INJECT_THRESHOLD:
        return []
    return list(regime.bearish_vehicles)


def snapshot_market_regime_field(regime: MarketRegime | None) -> dict[str, Any] | None:
    """Subset attached to each per-symbol snapshot."""
    if regime is None:
        return None
    return {
        "direction": regime.direction,
        "conviction": regime.conviction,
        "rationale": regime.rationale,
    }
