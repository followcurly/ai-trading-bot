"""Red-day backtest sidecar — never submits orders or touches paper trades.db."""

from trader.backtest.engine import (
    BacktestParams,
    run_backtest,
    run_lookback_compare,
    run_strategy_compare,
    run_universe_compare,
)

__all__ = [
    "BacktestParams",
    "run_backtest",
    "run_lookback_compare",
    "run_strategy_compare",
    "run_universe_compare",
]
