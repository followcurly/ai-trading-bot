#!/usr/bin/env python3
"""Paper promotion metrics for a trading profile (see docs/PAPER_PROMOTION.md)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trader.research.eval_pack import PROMOTION_THRESHOLDS, build_eval_pack


def _md(pack: dict) -> str:
    m = pack["promotion_metrics"]
    t = pack["thresholds"]
    lines = [
        f"# Profile metrics — `{pack['profile']}` ({pack['days']}d)",
        "",
        f"- Generated: {pack['generated_at']}",
        "",
        "## Promotion gate",
        "",
        f"| Metric | Value | Threshold | Pass |",
        f"| --- | --- | --- | --- |",
        f"| Closed trades | {m['closed_trades']} | ≥ {t['min_closed_trades']} | {'yes' if m['pass_closed_trades'] else 'no'} |",
        f"| Win rate | {m['win_rate']:.1%} | ≥ {t['min_win_rate']:.0%} | {'yes' if m['pass_win_rate'] else 'no'} |",
        f"| Profit factor | {m['profit_factor']:.2f} | ≥ {t['min_profit_factor']} | {'yes' if m['pass_profit_factor'] else 'no'} |",
        f"| Max drawdown | {m['max_drawdown_pct']:.1%} | ≤ {t['max_drawdown_pct']:.0%} | {'yes' if m['pass_drawdown'] else 'no'} |",
        "",
        f"**Overall:** {'PASS' if m['pass_all'] else 'FAIL'}",
        "",
        "## Actions",
        "",
        "| Action | Count |",
        "| --- | --- |",
    ]
    for k, v in sorted((pack.get("by_action") or {}).items()):
        lines.append(f"| {k} | {v} |")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description="Profile paper promotion metrics")
    ap.add_argument("--profile", default=None, help="default | yolo_options")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--db", type=Path, default=None)
    ap.add_argument("--format", choices=("json", "md"), default="json")
    args = ap.parse_args()
    pack = build_eval_pack(db_path=args.db, days=args.days, profile=args.profile)
    if args.format == "md":
        print(_md(pack))
    else:
        print(json.dumps(pack, indent=2, default=str))


if __name__ == "__main__":
    main()
