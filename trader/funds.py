"""Load sleeve / fund universe from data/funds.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from trader.config import REPO_ROOT

DEFAULT_FUNDS_PATH = REPO_ROOT / "data" / "funds.yaml"


@dataclass(frozen=True)
class Sleeve:
    name: str
    target_pct: float
    primary: str
    tickers: tuple[str, ...]


@dataclass(frozen=True)
class FundUniverse:
    sleeves: tuple[Sleeve, ...]

    def all_tickers(self) -> tuple[str, ...]:
        seen: list[str] = []
        for s in self.sleeves:
            for t in s.tickers:
                if t not in seen:
                    seen.append(t)
        return tuple(seen)

    def sleeve_for(self, symbol: str) -> Sleeve | None:
        sym = symbol.strip().upper()
        for s in self.sleeves:
            if sym in s.tickers:
                return s
        return None


@lru_cache(maxsize=4)
def load_funds(path: str | None = None) -> FundUniverse:
    p = Path(path) if path else DEFAULT_FUNDS_PATH
    raw: dict[str, Any] = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    sleeves_raw = raw.get("sleeves") or {}
    sleeves: list[Sleeve] = []
    for name, cfg in sleeves_raw.items():
        tickers = tuple(
            str(t).strip().upper() for t in (cfg.get("tickers") or []) if str(t).strip()
        )
        primary = str(cfg.get("primary") or (tickers[0] if tickers else "")).upper()
        try:
            target = float(cfg.get("target_pct", 0))
        except (TypeError, ValueError):
            target = 0.0
        if not tickers or not primary:
            continue
        sleeves.append(
            Sleeve(
                name=str(name),
                target_pct=target,
                primary=primary,
                tickers=tickers,
            )
        )
    return FundUniverse(sleeves=tuple(sleeves))
