"""Structured snapshot → Haiku trader JSON decision (single model call per symbol)."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import anthropic

log = logging.getLogger("trader")

from trader.config import (
    ANTHROPIC_MODEL_HAIKU,
    BRAIN_TRADER_MODEL,
    TRADING_PROFILE,
)
from trader.profile import default_fallback_strategy, get_profile

try:
    _TRADER_FEEDBACK_MAX_AGE_DAYS = float(os.getenv("TRADER_FEEDBACK_MAX_AGE_DAYS", "8"))
except ValueError:
    _TRADER_FEEDBACK_MAX_AGE_DAYS = 8.0


def _weekly_feedback_prefix() -> str:
    if os.getenv("TRADER_FEEDBACK_DISABLE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return ""
    path_raw = (os.getenv("TRADER_FEEDBACK_PATH") or "").strip()
    if path_raw:
        p = Path(path_raw).expanduser()
    else:
        try:
            from trader.reporting.weekly_report import reports_dir

            p = reports_dir() / "TRADER_FEEDBACK.md"
        except Exception:
            return ""
    if not p.is_file():
        return ""
    age_sec = time.time() - p.stat().st_mtime
    if age_sec > _TRADER_FEEDBACK_MAX_AGE_DAYS * 86400:
        return ""
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")[:2500]
    except OSError:
        return ""
    if not raw.strip():
        return ""
    return (
        "=== LAST WEEKLY REVIEW NOTES (historical critique; NOT live market data) ===\n"
        + raw.strip()
        + "\n\n=== END REVIEW NOTES ===\n\n"
    )


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


# ---------------------------------------------------------------------------
# Shared trading rules (trader must obey)
# ---------------------------------------------------------------------------
_SHARED_RULES = """
## Allowed strategies (pick exactly one when not HOLD)
- long_stock — buy/sell shares with explicit stop_loss and take_profit prices.
- long_call / long_put — single-leg long options only (defined risk). When action is BUY or SELL you MUST set option_expiry (YYYY-MM-DD) and option_strike (number). For BUY, size_pct caps premium spend (contracts sized from quotes × 100). For SELL, close an existing long option; executor sells the full open quantity for that contract.
- csp — cash-secured put (must be able to cover assignment).
- vertical_spread — bull put or bear call credit spread (defined risk only).

## Hard rules
- Max position size: 5% of account equity (size_pct ≤ 0.05).
- No naked options. No undefined-risk structures.
- confidence MUST be a number in [0, 1] (inclusive). If below the risk-engine floor (often 0.65; can be higher in elevated VIX when regime rules are enabled), use action HOLD.
- Use BUY or SELL only when you have a clear, indicator-aligned edge; otherwise HOLD.
- **data_quality:** when the snapshot includes `data_quality.missing_critical` and it is **non-empty**, you MUST respond with **action HOLD** and briefly cite incomplete data in `rationale` — do not BUY or SELL on a broken snapshot. `missing_optional` alone is normal (API keys off, etc.); you may still trade if price + core indicators support it.

## Options vs stock (balanced)
- When directional conviction is **high** (e.g. RSI stretched with MACD aligned, **adx_14 > 20**, **stoch_rsi_k** confirming) AND **vix.regime** is **calm** or **normal** (when vix is in the snapshot), **prefer** `long_call` (bullish) or `long_put` (bearish) over `long_stock` for convexity — still respect size_pct and allowlist.
- Use **at least ~7 calendar days** to **option_expiry** when opening new long options (risk engine enforces a minimum DTE). Set **option_strike** near **`options.atm.strike`** (same as **`options.atm_strike`**; ATM ± 1 strike in **`options.strikes_ring`** when present); otherwise nearest round strike to current price.
- If `vix` is missing or regime is **elevated** / **stress**, default to **long_stock** or **HOLD** unless the edge is overwhelming.

## Regime pre-brain (`market_regime` when present)
- Cross-asset verdict only: `direction` (`bullish` / `bearish` / `neutral`), `conviction` (0–1), `rationale`. **Neutral** = no macro tilt from the pre-brain; still trade the tape.
- **Bearish regime + tape:** when `market_regime.direction == "bearish"` with meaningful conviction AND (**RSI_14 > 75** with MACD rolling / negative divergence OR price at/above **bb_upper** OR **OBV** declining on a price rally) AND **adx_14 > 20** — prefer **`long_put`** on the underlying or **`long_call` / `long_stock`** on inverse/volatility names already on the watchlist (e.g. SQQQ, UVIX).
- **Bullish regime:** when `market_regime.direction == "bullish"`, favor trend-long / `long_call` when VIX + indicators allow; do not lean short without the same reversal stack.

## Snapshot fields (when present)
- position — if held=true you already own shares (qty, avg_entry, unrealized_pnl); do not recommend BUY to add unless adding is clearly justified; prefer SELL or HOLD when reducing risk. If held=false, **do NOT** propose `action=SELL strategy=long_stock` — there is nothing to sell (would attempt a naked short, which is forbidden). If position has `open_options`, you already hold options contracts on this underlying — factor in existing contracts before proposing new BUY entries; avoid accumulating more than 3 contracts on the same underlying/expiry.
- open_orders — list of working/HELD orders for this symbol AND its options (e.g. bracket take-profit limit and stop-loss legs). Includes pending option orders. If a working SELL (any type — LIMIT, STOP) already covers the position quantity, **do NOT** pitch another SELL; HOLD instead. The pending bracket leg will exit on its own. Only pitch a fresh SELL if existing exit orders are insufficient or directionally wrong (e.g. exit price now far from market and edge has clearly shifted).
- portfolio_context — full list of all open positions across the account (symbol, qty, unrealized_pnl, market_value). When `sector_notional_pct` is present, it maps sector label → fraction of equity already in that sector; respect concentration. Use this for **entry decisions only** — it is context for sizing new BUYs, not a signal to exit. Specifically: (1) if the same underlying already has 3+ open contracts in portfolio_context, do NOT propose a new BUY for that symbol (HOLD instead); (2) use total account exposure to avoid over-concentrating; (3) do NOT use portfolio_context as a reason to SELL or exit an existing position — exit decisions must be driven by price action, stops, or take-profit levels, not by the fact that a position exists.
- data_quality — `missing_critical` / `missing_optional` / `staleness_sec`; see hard rules above.
- symbol_sector — GICS-style label for the scanned equity (best-effort); use with portfolio_context for concentration awareness.
- earnings_days_away / next_earnings_date — do not add aggressive new risk into imminent earnings unless the edge clearly outweighs event risk.
- news_headlines — soft context only; never trade headlines alone without price/indicator alignment.
- indicators.prev_close, change_pct, vwap, volume_ratio, adx_14, stoch_rsi_k, stoch_rsi_d — intraday + trend/strength context; never trade on one indicator alone.
- **indicators.bb_upper / bb_lower:** price at/near **bb_upper** with **RSI_14 > 75** = overextended (bearish setups when regime/tape agree); price at/near **bb_lower** with **RSI_14 < 30** = oversold (bullish bounce setups when tape agrees).
- **indicators.obv:** declining OBV while price rallies = bearish volume divergence; rising OBV on a pullback = bullish accumulation.
- macro (when present) — FRED-style fields e.g. dgs10, dgs2, t10y2y, cpi_yoy, unrate; regime context only.
- vix.level, vix.regime, vix.change_pct — volatility regime; soft filter, not a standalone signal.
- **market_regime** — pre-brain cross-asset verdict (`direction`, `conviction`, `rationale`); see "Regime pre-brain" above.
- fear_greed, earnings_calendar[], analyst_recommendation — supporting context only.
- options.atm / options.strikes_ring — listed ATM±strikes with call/put iv and mid when data exists; prices are indicative.
- **Cross-asset rule:** macro, VIX, Fear & Greed, Finnhub analyst recommendation, and options-chain snippets are **supporting context** only — you still need price + indicator alignment on the underlying before BUY/SELL.
"""

_JSON_SHAPE = """
## Required JSON shape (all keys required; evidence optional)
{
  "action": "BUY" | "SELL" | "HOLD",
  "symbol": "TICKER",
  "strategy": "long_stock" | "long_call" | "long_put" | "csp" | "vertical_spread",
  "confidence": 0.0,
  "size_pct": 0.01,
  "stop_loss": 0.0,
  "take_profit": 0.0,
  "rationale": "one short sentence",
  "option_expiry": "",
  "option_strike": 0.0,
  "evidence": ["optional bullet ≤120 chars", "max five items total"]
}

For long_stock HOLD entries, use option_expiry "" and option_strike 0. For long_call/long_put with BUY or SELL, option_expiry must be YYYY-MM-DD and option_strike must match a listed strike; stop_loss and take_profit may be 0.

Omit "evidence" entirely if you have nothing to add. If present, use at most 5 short strings.

OUTPUT: ONE JSON object only. No markdown, no code fences, no commentary before or after the JSON.
"""

_YOLO_RULES = """
## YOLO options profile (active)
- **Strategies:** `long_call` and `long_put` ONLY — never `long_stock`, `csp`, or `vertical_spread`.
- **Sizing:** use `size_pct` up to the profile max (often ~12% of equity per idea); prefer 2–5 contracts when confidence ≥ 0.75 and liquidity is good.
- **DTE:** target **1–14 calendar days** to `option_expiry` (minimum enforced by risk engine, often 1d). Prefer nearest weekly with liquid bid/ask.
- **Strikes:** ATM or ±1 strike from `options.atm` / `options.strikes_ring`.
- **Universe:** high-beta / leveraged names on the watchlist (e.g. TQQQ, SOXL, TSLL, SQQQ, UVIX, NVDA).
- **Confidence:** output **≥ 0.70** only when ADX > 20, MACD aligns with direction, and tape matches `market_regime`; otherwise HOLD with confidence < 0.65.
- **Bearish:** `long_put` on weak underlyings or `long_call` on inverse/vol tickers (SQQQ, UVIX) when regime is bearish.
- **Exits:** stops/take_profit may be 0; position-health automation manages winners (moonshot ladder) — do not SELL early without a clear reversal stack.
"""

TRADER_SYSTEM_PROMPT = f"""You are a **directional momentum trader** — you follow momentum in either direction. Bullish tape → long calls or stock. Bearish tape → long puts or inverse vehicles. You do not have a long bias; you have a **momentum bias**. You still love convexity (options) when VIX is calm and every hard rule below allows it.

INPUT: One JSON object — the market snapshot.

{_SHARED_RULES}

## Trader voice
- Prefer action when RSI/MACD/ADX/stoch align with **market_regime** and price; call out the **fast** edge.
- When in doubt between stock and options, lean options **only** when hard rules + VIX allow.

## Examples (shape only; use live snapshot values)
BEARISH: {{"action":"BUY","symbol":"QQQ","strategy":"long_put","confidence":0.73,"size_pct":0.02,"stop_loss":0,"take_profit":0,"option_expiry":"2026-06-20","option_strike":720,"rationale":"Bearish regime + RSI 82 + price at bb_upper + OBV diverging"}}

{_JSON_SHAPE}
"""

YOLO_TRADER_SYSTEM_PROMPT = f"""You are an **aggressive short-dated options trader** (YOLO profile). You trade convexity on liquid high-beta names — no stock entries.

INPUT: One JSON object — the market snapshot.
Trading profile: **{TRADING_PROFILE}**

{_SHARED_RULES}
{_YOLO_RULES}

## Trader voice
- Act when momentum + regime + indicators align; size up when conviction is real.
- Default to `long_call` / `long_put` only; cite fast edge in `rationale`.

## Examples (shape only)
BULLISH: {{"action":"BUY","symbol":"TQQQ","strategy":"long_call","confidence":0.76,"size_pct":0.08,"stop_loss":0,"take_profit":0,"option_expiry":"2026-05-23","option_strike":65,"rationale":"ADX 28 MACD+ regime bullish stoch confirms"}}
BEARISH: {{"action":"BUY","symbol":"SQQQ","strategy":"long_call","confidence":0.74,"size_pct":0.06,"stop_loss":0,"take_profit":0,"option_expiry":"2026-05-23","option_strike":18,"rationale":"Bearish regime gap-down hedge vehicle"}}

{_JSON_SHAPE}
"""

class _ModelParseError(Exception):
    """Raised after all parse retries fail. Carries a short snippet for logs."""

    def __init__(self, snippet: str):
        super().__init__("Model response not parseable as JSON after retries")
        self.snippet = snippet


def _parse_json_response(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        text = text.removeprefix("json").strip()
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError("No JSON object in model response")
    return json.loads(m.group(0))


def _messages_create(
    client: anthropic.Anthropic,
    model: str,
    system: str | list,
    user_content: str,
    max_tokens: int,
) -> str:
    try:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )
    except TypeError:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system if isinstance(system, str) else system[0],
            messages=[{"role": "user", "content": user_content}],
        )
    return response.content[0].text


def _call_and_parse(
    client: anthropic.Anthropic,
    model: str,
    system: list,
    user_content: str,
    max_tokens: int,
) -> dict:
    """Single call → JSON parse, with up to two retries on parse failure."""
    text = _messages_create(client, model, system, user_content, max_tokens)
    try:
        return _parse_json_response(text)
    except (ValueError, json.JSONDecodeError):
        pass

    retry_content = (
        user_content
        + "\n\nREMINDER: Return EXACTLY ONE valid JSON object matching the required shape. No commentary, no code fences, no prose."
    )
    text2 = _messages_create(client, model, system, retry_content, max_tokens)
    try:
        return _parse_json_response(text2)
    except (ValueError, json.JSONDecodeError):
        pass

    retry2_content = (
        user_content
        + "\n\nFINAL ATTEMPT — STRICT MODE.\n"
        "Respond with ONLY a single raw JSON object. Begin your reply with '{' and end with '}'. "
        "No surrounding text, no markdown fences, no commentary, no apologies, no analysis. "
        "If you cannot produce the required decision, output the safest valid HOLD shape with confidence=0."
    )
    text3 = _messages_create(client, model, system, retry2_content, max_tokens + 200)
    try:
        return _parse_json_response(text3)
    except (ValueError, json.JSONDecodeError):
        snippet = (text3 or "")[:240].replace("\n", " ")
        raise _ModelParseError(snippet) from None


def _system_cached(system_text: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "text",
            "text": system_text,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _trader_system_prompt() -> str:
    if get_profile().brain_variant == "yolo_options":
        return YOLO_TRADER_SYSTEM_PROMPT
    return TRADER_SYSTEM_PROMPT


def _safe_trader_fallback(snapshot: dict, snippet: str) -> dict:
    """Deterministic safe-HOLD pitch when the trader LLM call cannot be parsed."""
    sym = ((snapshot.get("symbol") or snapshot.get("ticker") or "") or "").strip().upper() or "SPY"
    strat = default_fallback_strategy()
    return {
        "action": "HOLD",
        "symbol": sym,
        "strategy": strat,
        "confidence": 0.0,
        "size_pct": 0.0,
        "stop_loss": 0.0,
        "take_profit": 0.0,
        "option_expiry": "",
        "option_strike": 0,
        "rationale": f"safe_hold:trader_parse_fail:{snippet}",
        "_parse_fallback": True,
    }


def _call_trader(snapshot: dict) -> dict:
    client = _client()
    user_content = _weekly_feedback_prefix() + json.dumps(snapshot)
    try:
        return _call_and_parse(
            client,
            BRAIN_TRADER_MODEL,
            _system_cached(_trader_system_prompt()),
            user_content,
            max_tokens=450,
        )
    except _ModelParseError as e:
        sym = ((snapshot.get("symbol") or "") or "").strip().upper()
        log.warning("trader_parse_fallback symbol=%s snippet=%s", sym, e.snippet)
        return _safe_trader_fallback(snapshot, e.snippet)


def _float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _hold_shell(pitch: dict, rationale: str = "Guard hold") -> dict:
    sym = (pitch.get("symbol") or "").strip().upper() or "SPY"
    strat = (pitch.get("strategy") or default_fallback_strategy()).strip().lower()
    if strat not in get_profile().allowed_strategies:
        strat = default_fallback_strategy()
    return {
        "action": "HOLD",
        "symbol": sym,
        "strategy": strat,
        "confidence": min(_float(pitch.get("confidence"), 0.4), 0.64),
        "size_pct": 0.0,
        "stop_loss": 0.0,
        "take_profit": 0.0,
        "rationale": rationale,
        "option_expiry": "",
        "option_strike": 0.0,
    }


_OPEN_ACTIVE_STATUSES = {
    "NEW",
    "ACCEPTED",
    "PENDING_NEW",
    "PARTIALLY_FILLED",
    "ACCEPTED_FOR_BIDDING",
    "HELD",
    "REPLACED",
}


def _pending_sell_qty(open_orders: list[dict[str, Any]]) -> float:
    """Sum of share-quantities reserved by working SELL orders (any type)."""
    total = 0.0
    for o in open_orders or []:
        side = (o.get("side") or "").upper()
        status = (o.get("status") or "").upper()
        if "SELL" not in side:
            continue
        if status and status not in _OPEN_ACTIVE_STATUSES:
            continue
        try:
            total += float(o.get("qty") or 0)
        except (TypeError, ValueError):
            continue
    return total


def _pre_flight_guard(snapshot: dict, pitch: dict) -> str | None:
    """Return a short reason string if the trader pitch is incoherent given the snapshot."""
    action = (pitch.get("action") or "").upper()
    strategy = (pitch.get("strategy") or "").lower()
    if action != "SELL" or strategy != "long_stock":
        return None

    pos = snapshot.get("position") or {}
    held = bool(pos.get("held"))
    if not held:
        return "no_long_stock_position"

    qty_held = _float(pos.get("qty"), 0.0)
    pending = _pending_sell_qty(snapshot.get("open_orders") or [])
    if qty_held > 0 and pending >= qty_held:
        return "exit_already_pending"
    return None


def _strip_for_risk(d: dict[str, Any]) -> dict[str, Any]:
    """Remove keys risk/executor should not see."""
    skip = ("debate", "risk_exit_kind")
    return {k: v for k, v in d.items() if k not in skip}


def get_decision(snapshot: dict) -> dict[str, Any]:
    """Return trade decision + `debate` block for the journal (trader-only)."""
    pitch = _call_trader(snapshot)
    guard_reason = _pre_flight_guard(snapshot, pitch)
    if guard_reason is not None:
        held = _hold_shell(pitch, rationale=f"Pre-flight guard: {guard_reason}")
        return {
            **held,
            "debate": {
                "trader": pitch,
                "analyst_skipped": True,
                "reason": f"pre_flight:{guard_reason}",
            },
        }

    clean = _strip_for_risk(pitch)
    return {
        **clean,
        "debate": {
            "mode": "single_trader_haiku",
            "trader": pitch,
        },
    }
