"""US equity RTH calendar (America/New_York) — shared by bot and web UI."""

from __future__ import annotations

import os
from datetime import date, datetime, time as time_of_day

import pytz

ET = pytz.timezone("America/New_York")

_DEFAULT_NYSE_HOLIDAYS = (
    "2025-01-01,2025-01-20,2025-02-17,2025-04-18,2025-05-26,2025-06-19,"
    "2025-07-04,2025-09-01,2025-11-27,2025-12-25,"
    "2026-01-01,2026-01-19,2026-02-16,2026-04-03,2026-05-25,2026-06-19,"
    "2026-07-03,2026-09-07,2026-11-26,2026-12-25"
)


def nyse_holiday_dates() -> set[date]:
    raw = os.getenv("NYSE_HOLIDAYS", "").strip()
    blob = _DEFAULT_NYSE_HOLIDAYS + ("," + raw if raw else "")
    out: set[date] = set()
    for part in blob.split(","):
        p = part.strip()
        if not p:
            continue
        try:
            y, m, d = (int(x) for x in p.split("-", 2))
            out.add(date(y, m, d))
        except ValueError:
            continue
    return out


def is_us_equity_rth(now_et: datetime) -> bool:
    """Mon–Fri, 09:30–16:00 ET; excludes NYSE holidays (defaults + NYSE_HOLIDAYS)."""
    if now_et.weekday() >= 5:
        return False
    if now_et.date() in nyse_holiday_dates():
        return False
    t = now_et.time()
    return time_of_day(9, 30) <= t < time_of_day(16, 0)
