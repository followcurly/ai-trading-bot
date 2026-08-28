"""Shared Alpaca clients — trading base URL must be host only (SDK appends /v2/...)."""

from __future__ import annotations

import os
from functools import lru_cache

from alpaca.data.historical import OptionHistoricalDataClient, StockHistoricalDataClient
from alpaca.data.historical.screener import ScreenerClient
from alpaca.trading.client import TradingClient

from trader.config import ALPACA_BASE_URL_NORMALIZED, ALPACA_PAPER


@lru_cache(maxsize=1)
def data_client() -> StockHistoricalDataClient:
    """Market data API: paper and live keys both use the production data host (`sandbox=False`).

    Alpaca returns 401 on `data.sandbox` for typical paper keys. Use **IEX** feed on requests
    (see `data_feed`) so free-tier accounts avoid SIP subscription errors.
    """
    return StockHistoricalDataClient(
        api_key=os.environ["ALPACA_API_KEY"],
        secret_key=os.environ["ALPACA_SECRET_KEY"],
        sandbox=False,
    )


@lru_cache(maxsize=1)
def option_data_client() -> OptionHistoricalDataClient:
    """Options market data (quotes/trades). Same keys as stocks; `sandbox=False` (see data_client)."""
    return OptionHistoricalDataClient(
        api_key=os.environ["ALPACA_API_KEY"],
        secret_key=os.environ["ALPACA_SECRET_KEY"],
        sandbox=False,
    )


@lru_cache(maxsize=1)
def screener_client() -> ScreenerClient:
    """Alpaca Market Data Screener (most-actives, market-movers)."""
    return ScreenerClient(
        api_key=os.environ["ALPACA_API_KEY"],
        secret_key=os.environ["ALPACA_SECRET_KEY"],
    )


@lru_cache(maxsize=1)
def trading_client() -> TradingClient:
    """Trading API: optional `url_override` only after stripping `/v2` (see `trader.config`)."""
    kw: dict = {
        "api_key": os.environ["ALPACA_API_KEY"],
        "secret_key": os.environ["ALPACA_SECRET_KEY"],
        "paper": ALPACA_PAPER,
    }
    if ALPACA_BASE_URL_NORMALIZED:
        kw["url_override"] = ALPACA_BASE_URL_NORMALIZED
    return TradingClient(**kw)
