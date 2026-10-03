"""Backtest-only ETF universes (does not change paper funds.yaml)."""

from __future__ import annotations

from trader.funds import FundUniverse, Sleeve, load_funds

# Presets for yield vs appreciation experiments
UNIVERSE_PRESETS: dict[str, str] = {
    "balanced": "Paper mix (VOO / SCHD / QQQM sleeves)",
    "high_yield": "High yield — SCHD, VYM, JEPI, plus some VOO",
    "high_appreciation": "High appreciation — QQQM/QQQ/VUG/SCHG, plus some VOO",
}


def resolve_universe(name: str | None = None) -> FundUniverse:
    key = (name or "balanced").strip().lower()
    if key in ("", "balanced", "default", "funds", "funds.yaml"):
        return load_funds()

    if key in ("high_yield", "yield", "income", "dividend"):
        return FundUniverse(
            sleeves=(
                Sleeve(
                    name="income",
                    target_pct=0.70,
                    primary="SCHD",
                    tickers=("SCHD", "VYM", "JEPI", "DGRO", "VIG"),
                ),
                Sleeve(
                    name="core",
                    target_pct=0.30,
                    primary="VOO",
                    tickers=("VOO", "VTI", "SPYM"),
                ),
            )
        )

    if key in ("high_appreciation", "appreciation", "growth", "high_growth"):
        return FundUniverse(
            sleeves=(
                Sleeve(
                    name="growth",
                    target_pct=0.70,
                    primary="QQQM",
                    tickers=("QQQM", "QQQ", "SCHG", "VUG", "VGT"),
                ),
                Sleeve(
                    name="core",
                    target_pct=0.30,
                    primary="VOO",
                    tickers=("VOO", "VTI", "SPYM"),
                ),
            )
        )

    raise ValueError(
        f"unknown universe {name!r}; use balanced | high_yield | high_appreciation"
    )


def universe_label(name: str | None) -> str:
    key = (name or "balanced").strip().lower()
    return UNIVERSE_PRESETS.get(key, key)
