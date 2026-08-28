"""OCC option symbol parsing (infra helper; no order placement)."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any


def parse_occ_us_option_symbol(osi: str | None) -> dict[str, Any] | None:
    """Parse Alpaca US equity option symbol (OCC-style), e.g. ``NOK260515C00013500``."""
    if not osi:
        return None
    s = str(osi).strip().upper()
    m = re.match(r"^(.+?)(\d{6})([CP])(\d{8})$", s)
    if not m:
        return None
    root, ymd, cp, strike_raw = m.groups()
    if not root:
        return None
    try:
        exp = datetime.strptime(ymd, "%y%m%d").date()
        strike = int(strike_raw, 10) / 1000.0
    except ValueError:
        return None
    if strike <= 0:
        return None
    strat = "long_call" if cp == "C" else "long_put"
    return {
        "underlying": root,
        "expiry": exp,
        "expiry_iso": exp.isoformat(),
        "is_call": cp == "C",
        "strategy": strat,
        "strike": strike,
    }
