"""CLI: python -m trader.backtest run|show|compare"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


def _base_params(args: argparse.Namespace):
    from trader.backtest.engine import BacktestParams

    ma = getattr(args, "ma_filter", None)
    if ma in (0, "0", "none", "None", None):
        ma_filter = None
    else:
        ma_filter = int(ma) if ma is not None else None

    return BacktestParams(
        start=_parse_date(args.start),
        end=_parse_date(args.end),
        cash=float(args.cash),
        cost_bps=float(args.cost_bps),
        fill_rule=args.fill_rule,
        mode=getattr(args, "mode", "buy_hold"),
        red_lookback=int(getattr(args, "red_lookback", 1)),
        min_consecutive_reds=int(getattr(args, "min_consecutive_reds", 1)),
        min_down_pct=0.0,
        ma_filter=ma_filter,
        dca_cadence=getattr(args, "dca_cadence", "monthly"),
        dca_months=int(getattr(args, "dca_months", 6)),
        universe=getattr(args, "universe", "balanced"),
        year_end_skim=bool(getattr(args, "year_end_skim", False)),
        skim_pct=float(getattr(args, "skim_pct", 0.10)),
        use_cache=not args.no_cache,
    )


def _print_summary(result: dict) -> None:
    m = result["metrics"]
    full = m.get("full") or {}
    mode = result.get("mode") or (result.get("params") or {}).get("mode")
    lb = result.get("red_lookback") or (result.get("params") or {}).get("red_lookback") or 1
    print(
        f"run_id={result['id']} mode={mode} lookback={lb}d "
        f"return={full.get('total_return', 0)*100:.2f}% "
        f"vs_spy={full.get('vs_spy', 0)*100:.2f}% "
        f"max_dd={full.get('max_drawdown', 0)*100:.2f}% "
        f"trades={m.get('trade_count')}"
    )


def cmd_run(args: argparse.Namespace) -> int:
    from trader.backtest.engine import run_backtest

    def progress(phase: str, info: dict) -> None:
        if phase == "bars" and info.get("kind") == "fetch":
            print(f"  bars {info.get('symbol')}: fetch", flush=True)
        elif phase == "sim":
            print(f"  simulating {info.get('days')} days mode={info.get('mode')}…", flush=True)
        elif phase == "done":
            print(f"  done run_id={info.get('run_id')} trades={info.get('trades')}", flush=True)

    params = _base_params(args)
    # CLI --min-down-pct accepts percent (1 = 1%) or fraction if <= 0.5
    raw = float(args.min_down_pct)
    params.min_down_pct = raw / 100.0 if raw > 1 else raw

    print(
        f"backtest mode={params.mode} lookback={params.red_lookback}d "
        f"consec={params.min_consecutive_reds} ma={params.ma_filter} "
        f"{params.start}→{params.end} cash={params.cash:.0f} "
        f"cost_bps={params.cost_bps} fill={params.fill_rule}",
        flush=True,
    )
    result = run_backtest(params, persist=True, progress=progress)
    _print_summary(result)
    m = result["metrics"]
    div = m.get("dividends") or {}
    print(
        f"IS return={(m.get('is') or {}).get('total_return', 0)*100:.2f}% "
        f"OOS return={(m.get('oos') or {}).get('total_return', 0)*100:.2f}% "
        f"split={m.get('split_date')}"
    )
    print(
        f"dividends≈${float(div.get('total_cash') or 0):,.0f} "
        f"by_symbol={div.get('by_symbol') or {}}"
    )
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    from trader.backtest.engine import run_lookback_compare

    def progress(phase: str, info: dict) -> None:
        if phase == "lookback":
            print(f"── lookback {info.get('red_lookback')}d ──", flush=True)
        elif phase == "sim":
            print(f"  simulating {info.get('days')} days…", flush=True)

    params = _base_params(args)
    raw = float(args.min_down_pct)
    params.min_down_pct = raw / 100.0 if raw > 1 else raw
    print(
        f"compare lookback 1d/2d/3d mode={params.mode} "
        f"{params.start}→{params.end} cash={params.cash:.0f}",
        flush=True,
    )
    out = run_lookback_compare(params, progress=progress)
    print()
    print("lookback | return | vs SPY | max DD | trades | run_id")
    for r in out["runs"]:
        m = r["metrics"]
        full = m.get("full") or {}
        lb = r.get("red_lookback") or 1
        print(
            f"  {lb}d     | "
            f"{full.get('total_return', 0)*100:6.2f}% | "
            f"{full.get('vs_spy', 0)*100:+6.2f}% | "
            f"{full.get('max_drawdown', 0)*100:6.2f}% | "
            f"{m.get('trade_count', 0):6} | "
            f"{r['id']}"
        )
    print(f"\nUI: /backtest/compare?ids={','.join(out['ids'])}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    from trader.backtest.store import load_run

    run = load_run(args.run_id)
    if not run:
        print(f"run not found: {args.run_id}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(run, indent=2, default=str))
        return 0
    m = run.get("metrics") or {}
    full = m.get("full") or {}
    print(f"id={run['id']} {run['start_date']}→{run['end_date']}")
    print(
        f"mode={run.get('mode')} cash={run['cash']} cost_bps={run['cost_bps']} "
        f"fill={run['fill_rule']} red_lookback={run.get('red_lookback', 1)}d"
    )
    print(
        f"return={float(full.get('total_return') or 0)*100:.2f}% "
        f"vs_spy={float(full.get('vs_spy') or 0)*100:.2f}% "
        f"max_dd={float(full.get('max_drawdown') or 0)*100:.2f}% "
        f"trades={m.get('trade_count')}"
    )
    for t in (run.get("trades") or [])[:25]:
        print(
            f"  {t['dt']} {t.get('side','buy').upper()} {t['symbol']} "
            f"qty={t['qty']:.4f} px={t['price']:.2f} sleeve={t.get('sleeve')}"
        )
    return 0


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--start", required=True, help="YYYY-MM-DD")
    p.add_argument("--end", required=True, help="YYYY-MM-DD")
    p.add_argument("--cash", type=float, default=100_000.0)
    p.add_argument("--cost-bps", type=float, default=5.0, dest="cost_bps")
    p.add_argument(
        "--fill-rule",
        choices=("next_open", "same_close"),
        default="next_open",
        dest="fill_rule",
    )
    p.add_argument(
        "--mode",
        choices=("lump_sum", "dca", "buy_hold"),
        default="dca",
    )
    p.add_argument(
        "--year-end-skim",
        action="store_true",
        dest="year_end_skim",
        help="Sell skim-pct of each holding at each year-end (add-on to any mode)",
    )
    p.add_argument(
        "--red-lookback",
        type=int,
        choices=(1, 2, 3),
        default=1,
        dest="red_lookback",
    )
    p.add_argument(
        "--min-consecutive-reds",
        type=int,
        choices=(1, 2, 3),
        default=1,
        dest="min_consecutive_reds",
    )
    p.add_argument(
        "--min-down-pct",
        type=float,
        default=0.0,
        dest="min_down_pct",
        help="Min drop vs lookback close; use 1 for 1%% or 0.01",
    )
    p.add_argument(
        "--ma-filter",
        default=None,
        dest="ma_filter",
        help="Only buy when SPY > SMA(N); 50/100/200 or omit",
    )
    p.add_argument(
        "--dca-cadence",
        choices=("weekly", "monthly"),
        default="monthly",
        dest="dca_cadence",
    )
    p.add_argument(
        "--dca-months",
        type=int,
        default=6,
        dest="dca_months",
        help="Deploy DCA over first N months then hold (0 = whole range)",
    )
    p.add_argument(
        "--universe",
        choices=("balanced", "high_yield", "high_appreciation"),
        default="balanced",
        help="ETF mix preset (backtest-only; paper funds.yaml unchanged)",
    )
    p.add_argument(
        "--skim-pct",
        type=float,
        default=0.10,
        dest="skim_pct",
        help="With --year-end-skim: fraction of each holding to sell at year-end",
    )
    p.add_argument("--no-cache", action="store_true")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m trader.backtest")
    sub = p.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="Run a backtest and save to runs.db")
    _add_common(run_p)
    run_p.set_defaults(func=cmd_run)

    cmp_p = sub.add_parser("compare", help="Compare buy-the-dip lookbacks 1d/2d/3d (hold forever)")
    _add_common(cmp_p)
    cmp_p.set_defaults(func=cmd_compare)

    show_p = sub.add_parser("show", help="Show a saved run")
    show_p.add_argument("run_id")
    show_p.add_argument("--json", action="store_true")
    show_p.set_defaults(func=cmd_show)

    args = p.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
