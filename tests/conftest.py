"""Shared fixtures: no network, no Alpaca keys, no writes to data/."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from trader.funds import FundUniverse, Sleeve
from trader.signal_red_day import DaySignal


@pytest.fixture
def universe() -> FundUniverse:
    return FundUniverse(
        sleeves=(
            Sleeve("core", 0.50, "VOO", ("VOO", "VTI", "SPYM")),
            Sleeve("dividend", 0.25, "SCHD", ("SCHD", "VYM")),
            Sleeve("growth", 0.25, "QQQM", ("QQQ", "QQQM")),
        )
    )


def pos(symbol: str, market_value: float) -> SimpleNamespace:
    return SimpleNamespace(symbol=symbol, market_value=str(market_value))


def sig(symbol: str, day_return: float | None) -> DaySignal:
    return DaySignal(
        symbol=symbol,
        last=100.0,
        prev_close=100.0,
        day_return=day_return,
        is_red=day_return is not None and day_return < 0,
    )
