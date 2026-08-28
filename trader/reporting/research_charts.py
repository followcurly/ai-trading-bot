"""SVG chart bundle for /research dashboard."""

from __future__ import annotations

from typing import Any

from trader.reporting.chart_svg import (
    decision_breakdown_svg,
    drawdown_curve_svg,
    equity_curve_svg,
    pnl_by_exit_reason_svg,
    promotion_gauge_svg,
)


def build_research_charts(pack: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    eq = pack.get("equity_series") or []
    if eq:
        out["equity_curve"] = equity_curve_svg(eq)
    dd = pack.get("drawdown_series") or []
    if dd:
        out["drawdown"] = drawdown_curve_svg(dd)
    by_action = pack.get("by_action") or {}
    if by_action:
        out["decisions"] = decision_breakdown_svg(by_action)
    promo = pack.get("promotion_metrics") or {}
    if promo:
        out["promotion"] = promotion_gauge_svg(promo)
    pnl_map = pack.get("pnl_by_exit_reason") or {}
    if pnl_map:
        out["pnl_by_exit"] = pnl_by_exit_reason_svg(pnl_map)
    return out
