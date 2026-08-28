"""Cash buckets for backtests: deployable vs idle skim vs idle dividends."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CashState:
    """Three cash ledgers — buys spend only ``free``; skim/div sit idle."""

    free: float = 0.0
    skim: float = 0.0
    div: float = 0.0

    @property
    def total(self) -> float:
        return float(self.free) + float(self.skim) + float(self.div)

    def snapshot(self) -> dict[str, float]:
        return {
            "cash": self.total,
            "cash_free": float(self.free),
            "cash_skim": float(self.skim),
            "cash_div": float(self.div),
        }

    def as_holder(self) -> dict[str, Any]:
        """Mutable holder for execute_buy / execute_sell."""
        return {"state": self}
