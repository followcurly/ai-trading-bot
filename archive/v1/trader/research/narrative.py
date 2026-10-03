"""Plain-English research narrative from an eval pack (no LLM)."""

from __future__ import annotations

from typing import Any

EXIT_GLOSSARY: dict[str, str] = {
    "profit_ladder": "Automation took partial/full profit at a ladder rung (+20% / +50% / +100% unrealized on the contract).",
    "trail_giveback": "Unrealized profit fell too far from its peak; automation closed to lock in what was left.",
    "options_hard_stop": "Contract hit the configured hard loss % (e.g. −65% unrealized).",
    "expiry_sweep": "Option was closed on expiration day before the cutoff to avoid assignment.",
    "emergency_daily_loss": "Session loss hit the daily threshold; bot flattened opens and halted new risk.",
    "bracket_take_profit": "Stock bracket take-profit limit filled at the broker.",
    "bracket_stop": "Stock bracket stop-loss filled at the broker.",
    "discretionary_sell": "Haiku asked for a SELL and risk allowed it (not a rule-only exit).",
    "other": "Exit reason not classified — check cycle detail for rationale.",
}

ACTION_GLOSSARY: dict[str, str] = {
    "HOLD": "Model or risk said do nothing this cycle for that symbol.",
    "BUY": "Bot wanted to open or add risk (may still be blocked → HALT).",
    "SELL": "Bot wanted to close or reduce (stock/options).",
    "HALT": "Kill-switch: daily loss, drawdown from peak, or similar — no new risk.",
}


def build_research_narrative(pack: dict[str, Any]) -> dict[str, Any]:
    """Return summary bullets, warnings, and glossary-friendly stats."""
    by_action = pack.get("by_action") or {}
    by_exec = pack.get("by_execute_status") or {}
    promo = pack.get("promotion_metrics") or {}
    prof = pack.get("profile") or "default"
    days = int(pack.get("days") or 30)
    eq = pack.get("equity_series") or []
    exit_counts = pack.get("exit_reason_counts") or {}
    pnl_by_exit = pack.get("pnl_by_exit_reason") or {}

    total_cycles = sum(int(v) for v in by_action.values())
    holds = int(by_action.get("HOLD", 0))
    halts = int(by_action.get("HALT", 0))
    buys = int(by_action.get("BUY", 0))
    sells = int(by_action.get("SELL", 0))
    placed = int(by_exec.get("placed", 0))
    skipped = int(by_exec.get("skipped", 0))
    not_attempted = int(by_exec.get("not_attempted", 0))

    bullets: list[str] = []
    warnings: list[str] = []

    if total_cycles == 0:
        bullets.append(
            f"No journal cycles in the last {days} days for profile `{prof}` "
            "(or rows predate the trading_profile tag)."
        )
    else:
        bullets.append(
            f"In the last {days} days, {total_cycles} cycle rows tagged '{prof}': "
            f"{holds} HOLD, {buys} BUY pitches, {sells} SELL pitches, {halts} HALT."
        )
        if placed:
            bullets.append(
                f"{placed} orders reached Alpaca (placed); {skipped} skipped; "
                f"{not_attempted} HOLD/HALT with no order attempt."
            )
        if halts > buys and halts > 0:
            warnings.append(
                "HALT count is high — often daily loss (−2% or −5%) or drawdown-from-peak. "
                "Check daily_pnl on HALT rows in Cycles."
            )
        if buys > 0 and placed == 0:
            warnings.append(
                "BUY signals never placed — confidence gate, sector cap, PDT, liquidity, or HALT may be blocking."
            )

    if len(eq) >= 2:
        start_v, end_v = eq[0][1], eq[-1][1]
        delta = end_v - start_v
        pct = (delta / start_v * 100.0) if start_v > 0 else 0.0
        bullets.append(
            f"Journal equity snapshots: ${start_v:,.0f} → ${end_v:,.0f} "
            f"({delta:+,.0f}, {pct:+.1f}%) over the window."
        )
    elif len(eq) == 1:
        bullets.append(f"Only one equity snapshot in-window (${eq[0][1]:,.0f}).")

    if exit_counts:
        top = sorted(exit_counts.items(), key=lambda x: -x[1])[:5]
        parts = [f"{k} ({v})" for k, v in top]
        bullets.append(f"SELL exits by reason: {', '.join(parts)}.")

    if pnl_by_exit:
        worst = sorted(pnl_by_exit.items(), key=lambda x: x[1])[:3]
        best = sorted(pnl_by_exit.items(), key=lambda x: -x[1])[:3]
        if worst and worst[0][1] < 0:
            warnings.append(
                "Largest P&L drag from exits: "
                + ", ".join(f"{k} (${v:,.0f})" for k, v in worst if v < 0)
                + "."
            )
        if best and best[0][1] > 0:
            bullets.append(
                "Best exit buckets: "
                + ", ".join(f"{k} (+${v:,.0f})" for k, v in best if v > 0)
                + "."
            )

    if prof == "yolo_options":
        bullets.append(
            "YOLO profile: options-only entries, static high-beta watchlist, moonshot profit ladder, "
            "2‑min position health, extra scan slots — see Architecture."
        )

    headline = "Quiet period" if total_cycles < 5 else "Active period"
    if promo.get("pass_all"):
        headline = "Paper promotion gate: PASS (on tracked exits)"
    elif halts > placed and halts > 0:
        headline = "Risk-off: HALTs dominating"
    elif placed > 0:
        headline = "Trading activity with fills"

    return {
        "headline": headline,
        "bullets": bullets,
        "warnings": warnings,
        "action_glossary": ACTION_GLOSSARY,
        "exit_glossary": EXIT_GLOSSARY,
        "total_cycles": total_cycles,
    }
