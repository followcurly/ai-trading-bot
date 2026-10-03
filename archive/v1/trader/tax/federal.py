"""US federal tax estimates for individuals and trusts (planning only)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

# Tax Year 2025 published brackets (use for 2025–2026 planning until IRS finalizes 2026).
TAX_YEAR = 2025

Entity = Literal["individual_single", "individual_mfj", "trust"]

# Ordinary income brackets: list of (upper_bound_inclusive_or_None, rate)
INDIVIDUAL_SINGLE_ORDINARY = [
    (11_925, 0.10),
    (48_475, 0.12),
    (103_350, 0.22),
    (197_300, 0.24),
    (250_525, 0.32),
    (626_350, 0.35),
    (None, 0.37),
]
INDIVIDUAL_MFJ_ORDINARY = [
    (23_850, 0.10),
    (96_950, 0.12),
    (206_700, 0.22),
    (394_600, 0.24),
    (501_050, 0.32),
    (751_600, 0.35),
    (None, 0.37),
]
# Complex trusts / estates — compressed brackets (TY2025)
TRUST_ORDINARY = [
    (3_150, 0.10),
    (11_450, 0.24),
    (15_650, 0.35),
    (None, 0.37),
]

# Preferential LTCG / qualified dividend brackets
INDIVIDUAL_SINGLE_LTCG = [
    (48_350, 0.00),
    (533_400, 0.15),
    (None, 0.20),
]
INDIVIDUAL_MFJ_LTCG = [
    (96_700, 0.00),
    (600_050, 0.15),
    (None, 0.20),
]
# Trusts: preferential rates kick in quickly
TRUST_LTCG = [
    (3_150, 0.00),
    (15_450, 0.15),
    (None, 0.20),
]

STD_DED_SINGLE = 15_000
STD_DED_MFJ = 30_000
# Trusts: exemption is small / often unused when income distributed — default 100
TRUST_EXEMPTION = 100

NIIT_RATE = 0.038
NIIT_SINGLE = 200_000
NIIT_MFJ = 250_000
# Trusts: NIIT applies above a low threshold (~$15k)
NIIT_TRUST = 15_650


@dataclass
class TaxInputs:
    entity: Entity = "individual_single"
    ordinary_income: float = 0.0  # wages, interest, ordinary dividends, STCG, etc.
    qualified_dividends: float = 0.0
    long_term_gains: float = 0.0
    short_term_gains: float = 0.0  # taxed as ordinary
    other_investment_income: float = 0.0  # for NIIT MAGI proxy
    itemized_or_std_deduction: float | None = None  # None → default std/exemption
    tax_year: int = TAX_YEAR


def _tax_progressive(amount: float, brackets: list[tuple[float | None, float]]) -> float:
    if amount <= 0:
        return 0.0
    tax = 0.0
    prev = 0.0
    remaining = amount
    for upper, rate in brackets:
        if remaining <= 1e-9:
            break
        if upper is None:
            width = remaining
        else:
            width = min(remaining, max(0.0, upper - prev))
        tax += width * rate
        remaining -= width
        prev = upper if upper is not None else prev + width
    return tax


def _ordinary_brackets(entity: Entity):
    if entity == "individual_mfj":
        return INDIVIDUAL_MFJ_ORDINARY
    if entity == "trust":
        return TRUST_ORDINARY
    return INDIVIDUAL_SINGLE_ORDINARY


def _ltcg_brackets(entity: Entity):
    if entity == "individual_mfj":
        return INDIVIDUAL_MFJ_LTCG
    if entity == "trust":
        return TRUST_LTCG
    return INDIVIDUAL_SINGLE_LTCG


def _default_deduction(entity: Entity) -> float:
    if entity == "individual_mfj":
        return float(STD_DED_MFJ)
    if entity == "trust":
        return float(TRUST_EXEMPTION)
    return float(STD_DED_SINGLE)


def _niit_threshold(entity: Entity) -> float:
    if entity == "individual_mfj":
        return float(NIIT_MFJ)
    if entity == "trust":
        return float(NIIT_TRUST)
    return float(NIIT_SINGLE)


def calculate_federal_tax(inp: TaxInputs) -> dict[str, Any]:
    """Return a detailed federal tax estimate (not advice)."""
    stcg = max(0.0, float(inp.short_term_gains))
    ordinary_base = max(0.0, float(inp.ordinary_income)) + stcg
    qdiv = max(0.0, float(inp.qualified_dividends))
    ltcg = max(0.0, float(inp.long_term_gains))
    ded = (
        float(inp.itemized_or_std_deduction)
        if inp.itemized_or_std_deduction is not None
        else _default_deduction(inp.entity)
    )
    ded = max(0.0, ded)

    # Taxable ordinary after deduction (deduction applies to ordinary first)
    ordinary_taxable = max(0.0, ordinary_base - ded)
    unused_ded = max(0.0, ded - ordinary_base)
    # Preferential stack sits on top of ordinary taxable income
    pref = qdiv + ltcg
    # Excess deduction can shelter preferential income
    pref_taxable = max(0.0, pref - unused_ded)

    ordinary_tax = _tax_progressive(ordinary_taxable, _ordinary_brackets(inp.entity))

    # Preferential: tax the slice from ordinary_taxable .. ordinary_taxable+pref
    # Equivalent: tax(ordinary+pref) at LTCG brackets minus tax(ordinary) at LTCG brackets
    ltcg_brackets = _ltcg_brackets(inp.entity)
    pref_tax = _tax_progressive(ordinary_taxable + pref_taxable, ltcg_brackets) - _tax_progressive(
        ordinary_taxable, ltcg_brackets
    )

    income_tax = ordinary_tax + pref_tax

    # NIIT: 3.8% on lesser of net investment income or MAGI over threshold
    nii = qdiv + ltcg + stcg + max(0.0, float(inp.other_investment_income))
    # MAGI proxy ≈ ordinary + pref (simplified; no above-the-line adjustments)
    magi = ordinary_base + pref
    niit_base = min(nii, max(0.0, magi - _niit_threshold(inp.entity)))
    niit = niit_base * NIIT_RATE

    total = income_tax + niit
    taxable_income = ordinary_taxable + pref_taxable
    effective = (total / (ordinary_base + pref)) if (ordinary_base + pref) > 1e-6 else 0.0

    return {
        "tax_year": inp.tax_year,
        "entity": inp.entity,
        "inputs": asdict(inp),
        "deduction_used": ded,
        "ordinary_taxable": ordinary_taxable,
        "preferential_taxable": pref_taxable,
        "taxable_income": taxable_income,
        "ordinary_tax": ordinary_tax,
        "preferential_tax": pref_tax,
        "income_tax": income_tax,
        "niit": niit,
        "niit_base": niit_base,
        "total_federal": total,
        "effective_rate": effective,
        "marginal_ordinary_rate": _marginal_rate(ordinary_taxable, _ordinary_brackets(inp.entity)),
        "marginal_pref_rate": _marginal_rate(
            ordinary_taxable + pref_taxable, ltcg_brackets
        ),
        "notes": [
            f"Federal estimate for tax year {inp.tax_year} (planning only — not tax advice).",
            "Short-term gains are included in ordinary income.",
            "Qualified dividends + LTCG use preferential brackets stacked above ordinary taxable income.",
            "NIIT is a simplified MAGI proxy (ordinary + preferential).",
            "State tax, AMT, 3.8% Medicare nuances, and DNI/distribution deductions for trusts are not modeled.",
        ],
        "brackets": {
            "ordinary": _brackets_public(_ordinary_brackets(inp.entity)),
            "preferential": _brackets_public(ltcg_brackets),
            "niit_threshold": _niit_threshold(inp.entity),
            "niit_rate": NIIT_RATE,
        },
    }


def _marginal_rate(amount: float, brackets: list[tuple[float | None, float]]) -> float:
    if amount < 0:
        amount = 0.0
    prev = 0.0
    for upper, rate in brackets:
        if upper is None or amount <= upper + 1e-9:
            return rate
        prev = upper
    return brackets[-1][1]


def _brackets_public(brackets: list[tuple[float | None, float]]) -> list[dict[str, Any]]:
    out = []
    prev = 0.0
    for upper, rate in brackets:
        out.append({"from": prev, "to": upper, "rate": rate})
        prev = upper if upper is not None else prev
    return out


def entity_label(entity: Entity) -> str:
    return {
        "individual_single": "Individual — Single",
        "individual_mfj": "Individual — Married Filing Jointly",
        "trust": "Complex Trust / Estate",
    }.get(entity, entity)
