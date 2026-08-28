"""Sleeve weights from Alpaca positions; pick red buy candidates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from trader.funds import FundUniverse, Sleeve, load_funds
from trader.signal_red_day import DaySignal, day_signal


@dataclass(frozen=True)
class SleeveWeight:
    sleeve: Sleeve
    market_value: float
    weight: float
    target_pct: float
    underweight: bool
    gap_pct: float


@dataclass(frozen=True)
class BuyCandidate:
    sleeve: Sleeve
    symbol: str
    signal: DaySignal
    sleeve_weight: SleeveWeight


def position_market_values(positions: list[Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for p in positions:
        sym = (getattr(p, "symbol", None) or "").strip().upper()
        if not sym:
            continue
        try:
            mv = float(getattr(p, "market_value", None) or 0)
        except (TypeError, ValueError):
            mv = 0.0
        out[sym] = out.get(sym, 0.0) + mv
    return out


def sleeve_weights(
    equity: float,
    positions: list[Any],
    universe: FundUniverse | None = None,
) -> list[SleeveWeight]:
    uni = universe or load_funds()
    mvs = position_market_values(positions)
    eq = float(equity) if equity and float(equity) > 0 else 0.0
    rows: list[SleeveWeight] = []
    for sleeve in uni.sleeves:
        mv = sum(mvs.get(t, 0.0) for t in sleeve.tickers)
        w = (mv / eq) if eq > 0 else 0.0
        gap = sleeve.target_pct - w
        rows.append(
            SleeveWeight(
                sleeve=sleeve,
                market_value=mv,
                weight=w,
                target_pct=sleeve.target_pct,
                underweight=gap > 1e-6,
                gap_pct=gap,
            )
        )
    return rows


def pick_red_candidate_for_sleeve(
    sw: SleeveWeight,
    *,
    signals: dict[str, DaySignal] | None = None,
) -> BuyCandidate | None:
    """Prefer primary if red; else first red alternate in sleeve order."""
    if not sw.underweight:
        return None
    sleeve = sw.sleeve
    order = (sleeve.primary,) + tuple(t for t in sleeve.tickers if t != sleeve.primary)
    for sym in order:
        sig = signals[sym] if signals and sym in signals else day_signal(sym)
        if sig.is_red:
            return BuyCandidate(
                sleeve=sleeve, symbol=sym, signal=sig, sleeve_weight=sw
            )
    return None


def red_buy_candidates(
    equity: float,
    positions: list[Any],
    universe: FundUniverse | None = None,
) -> list[BuyCandidate]:
    """At most one candidate per underweight sleeve (red ticker required)."""
    uni = universe or load_funds()
    weights = sleeve_weights(equity, positions, uni)
    # Pre-fetch signals for all listed tickers once
    signals = {t: day_signal(t) for t in uni.all_tickers()}
    out: list[BuyCandidate] = []
    # Prefer largest gap first
    for sw in sorted(weights, key=lambda r: r.gap_pct, reverse=True):
        cand = pick_red_candidate_for_sleeve(sw, signals=signals)
        if cand is not None:
            out.append(cand)
    return out
