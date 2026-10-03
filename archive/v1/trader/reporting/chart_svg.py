"""Pure-Python SVG chart generators — no matplotlib, no dependencies.

All charts use CSS variables from the parent page so they respond to dark/light mode
automatically when embedded inline via Jinja's `| safe` filter.
"""

from __future__ import annotations

import html
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Colour palette — maps to base.html CSS variables
# ---------------------------------------------------------------------------
_C = {
    "fg":       "var(--fg)",
    "muted":    "var(--muted)",
    "border":   "var(--border)",
    "bg":       "var(--row-alt)",
    "accent":   "var(--accent)",
    "buy":      "var(--chip-buy)",
    "buy_bg":   "var(--chip-buy-bg)",
    "sell":     "var(--chip-sell)",
    "sell_bg":  "var(--chip-sell-bg)",
    "hold":     "var(--chip-hold)",
    "hold_bg":  "var(--chip-hold-bg)",
    "halt":     "var(--chip-halt)",
    "halt_bg":  "var(--chip-halt-bg)",
}

_ACTION_COLOR = {
    "BUY":  (_C["buy_bg"],  _C["buy"]),
    "SELL": (_C["sell_bg"], _C["sell"]),
    "HOLD": (_C["hold_bg"], _C["hold"]),
    "HALT": (_C["halt_bg"], _C["halt"]),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _esc(s: str) -> str:
    return html.escape(str(s))


def _fmt_k(v: float) -> str:
    """Format a dollar value compactly: 100234 → $100.2k"""
    if abs(v) >= 1_000_000:
        return f"${v/1_000_000:.1f}M"
    if abs(v) >= 1_000:
        return f"${v/1_000:.1f}k"
    return f"${v:.0f}"


# ---------------------------------------------------------------------------
# Equity curve (line chart)
# ---------------------------------------------------------------------------

def equity_curve_svg(
    rows: list[tuple[str, float]],   # [(iso_timestamp, equity_value), ...]
    width: int = 600,
    height: int = 220,
) -> str:
    """Return an inline SVG line chart of equity over time."""
    if len(rows) < 2:
        return _empty_chart("No equity data yet", width, height)

    pad_l, pad_r, pad_t, pad_b = 64, 16, 20, 40
    inner_w = width - pad_l - pad_r
    inner_h = height - pad_t - pad_b

    values = [r[1] for r in rows]
    times  = [r[0] for r in rows]
    v_min, v_max = min(values), max(values)
    v_range = v_max - v_min or 1.0

    def px(i: int) -> float:
        return pad_l + (i / (len(rows) - 1)) * inner_w

    def py(v: float) -> float:
        return pad_t + inner_h - ((v - v_min) / v_range) * inner_h

    # Build polyline points
    pts = " ".join(f"{px(i):.1f},{py(v):.1f}" for i, v in enumerate(values))

    # Fill polygon (area under line)
    fill_pts = (
        f"{px(0):.1f},{pad_t + inner_h} "
        + pts
        + f" {px(len(rows)-1):.1f},{pad_t + inner_h}"
    )

    # Y-axis grid lines (4 levels)
    grid_lines = []
    y_labels   = []
    for k in range(5):
        frac = k / 4
        v = v_min + frac * v_range
        y = py(v)
        grid_lines.append(
            f'<line x1="{pad_l}" y1="{y:.1f}" x2="{pad_l + inner_w}" y2="{y:.1f}" '
            f'stroke="{_C["border"]}" stroke-width="1" stroke-dasharray="4 3"/>'
        )
        y_labels.append(
            f'<text x="{pad_l - 6}" y="{y + 4:.1f}" text-anchor="end" '
            f'font-size="10" fill="{_C["muted"]}">{_esc(_fmt_k(v))}</text>'
        )

    # X-axis date labels (up to 5)
    x_labels = []
    n = len(rows)
    step = max(1, n // 5)
    for i in range(0, n, step):
        try:
            dt = datetime.fromisoformat(times[i].replace("Z", "+00:00"))
            label = dt.strftime("%-m/%-d")
        except Exception:
            label = str(i)
        x_labels.append(
            f'<text x="{px(i):.1f}" y="{pad_t + inner_h + 16}" text-anchor="middle" '
            f'font-size="10" fill="{_C["muted"]}">{_esc(label)}</text>'
        )

    # Delta annotation (top right)
    delta = values[-1] - values[0]
    sign  = "+" if delta >= 0 else ""
    delta_color = _C["buy"] if delta >= 0 else _C["sell"]
    delta_label = (
        f'<text x="{pad_l + inner_w}" y="{pad_t - 4}" text-anchor="end" '
        f'font-size="11" font-weight="600" fill="{delta_color}">'
        f'{sign}{_fmt_k(delta)}</text>'
    )

    svg = f"""<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg"
     style="width:100%;max-width:{width}px;display:block;overflow:visible">
  <defs>
    <linearGradient id="areafill" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{_C['accent']}" stop-opacity="0.25"/>
      <stop offset="100%" stop-color="{_C['accent']}" stop-opacity="0.02"/>
    </linearGradient>
  </defs>
  {"".join(grid_lines)}
  <polygon points="{fill_pts}" fill="url(#areafill)"/>
  <polyline points="{pts}"
    fill="none" stroke="{_C['accent']}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"/>
  {"".join(y_labels)}
  {"".join(x_labels)}
  {delta_label}
  <text x="{pad_l}" y="{pad_t - 4}" font-size="11" font-weight="600"
    fill="{_C['fg']}">Account equity</text>
</svg>"""
    return svg


# ---------------------------------------------------------------------------
# Horizontal bar chart (decisions or symbols)
# ---------------------------------------------------------------------------

def hbar_chart_svg(
    items: list[tuple[str, float, str, str]],  # (label, value, bar_fill, text_fill)
    title: str,
    width: int = 600,
    height: int | None = None,
    unit: str = "",
) -> str:
    """Horizontal bar chart. items = [(label, value, bar_color, text_color), ...]"""
    if not items:
        return _empty_chart(f"No {title.lower()} data", width, 120)

    row_h   = 34
    pad_l   = 72
    pad_r   = 60
    pad_t   = 28
    pad_b   = 12
    h       = height or (pad_t + pad_b + len(items) * row_h)
    inner_w = width - pad_l - pad_r

    max_val = max(v for _, v, *_ in items) or 1.0

    bars   = []
    labels = []
    vals   = []

    for k, (label, value, bar_fill, text_fill) in enumerate(items):
        y      = pad_t + k * row_h
        bar_w  = max(2.0, (value / max_val) * inner_w)
        cy     = y + row_h * 0.5

        bars.append(
            f'<rect x="{pad_l}" y="{y + 6:.1f}" width="{bar_w:.1f}" height="{row_h - 12}"'
            f' rx="4" fill="{bar_fill}" opacity="0.9"/>'
        )
        labels.append(
            f'<text x="{pad_l - 8}" y="{cy + 4:.1f}" text-anchor="end"'
            f' font-size="11" fill="{_C["fg"]}">{_esc(label)}</text>'
        )
        display = f"{int(value)}{unit}" if value == int(value) else f"{value:.1f}{unit}"
        vals.append(
            f'<text x="{pad_l + bar_w + 6:.1f}" y="{cy + 4:.1f}" text-anchor="start"'
            f' font-size="11" font-weight="600" fill="{text_fill}">{_esc(display)}</text>'
        )

    svg = f"""<svg viewBox="0 0 {width} {h}" xmlns="http://www.w3.org/2000/svg"
     style="width:100%;max-width:{width}px;display:block">
  <text x="0" y="16" font-size="11" font-weight="600" fill="{_C['fg']}">{_esc(title)}</text>
  {"".join(bars)}
  {"".join(labels)}
  {"".join(vals)}
</svg>"""
    return svg


# ---------------------------------------------------------------------------
# Decision breakdown bar chart
# ---------------------------------------------------------------------------

def decision_breakdown_svg(
    by_action: dict[str, int],
    width: int = 580,
) -> str:
    """Bar chart of BUY/SELL/HOLD/HALT decision counts."""
    order   = ["BUY", "SELL", "HOLD", "HALT"]
    items   = []
    for action in order:
        cnt = by_action.get(action, 0)
        if cnt == 0:
            continue
        bg, fg = _ACTION_COLOR.get(action, (_C["hold_bg"], _C["hold"]))
        items.append((action, float(cnt), bg, fg))

    if not items:
        return _empty_chart("No decisions recorded yet", width, 120)

    return hbar_chart_svg(items, "Decisions this week", width=width, unit=" cycles")


# ---------------------------------------------------------------------------
# Top symbols bar chart
# ---------------------------------------------------------------------------

def top_symbols_svg(
    top_symbols: list[dict[str, Any]],   # [{"symbol": "AAPL", "count": 12}, ...]
    width: int = 580,
) -> str:
    """Bar chart of most-watched symbols by cycle count."""
    if not top_symbols:
        return _empty_chart("No symbol data yet", width, 120)

    items = [
        (s["symbol"], float(s["count"]), _C["accent"], _C["accent"])
        for s in top_symbols[:8]
    ]
    return hbar_chart_svg(items, "Most-watched symbols", width=width, unit=" cycles")


# ---------------------------------------------------------------------------
# Equity time series from SQLite
# ---------------------------------------------------------------------------

def load_equity_series(db_path: Path, cutoff_iso: str) -> list[tuple[str, float]]:
    """Return [(iso_timestamp, equity)] rows since cutoff, sampled to ≤ 100 points."""
    if not db_path.is_file():
        return []
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """SELECT timestamp, equity FROM trades
               WHERE timestamp > ? AND equity IS NOT NULL
               ORDER BY id ASC""",
            (cutoff_iso,),
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return []

    if not rows:
        return []

    # Deduplicate to one point per hour to keep the chart readable
    seen: dict[str, float] = {}
    for r in rows:
        ts = str(r["timestamp"])
        # Bucket by hour: keep last value per hour
        try:
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            bucket = dt.strftime("%Y-%m-%dT%H")
        except ValueError:
            bucket = ts[:13]
        seen[bucket] = float(r["equity"])

    # Re-sort by bucket key (ISO order is lexicographic order for hourly buckets)
    pts = [(k + ":00:00+00:00", v) for k, v in sorted(seen.items())]

    # Sample down if still too many
    if len(pts) > 60:
        step = len(pts) // 60
        pts  = pts[::step]

    return pts


# ---------------------------------------------------------------------------
# Empty placeholder
# ---------------------------------------------------------------------------

def drawdown_curve_svg(
    rows: list[tuple[str, float]],
    width: int = 600,
    height: int = 220,
) -> str:
    """Line chart of drawdown % (values should be <= 0)."""
    if len(rows) < 2:
        return _empty_chart("No drawdown data yet", width, height)
    inverted = [(t, abs(min(0.0, v))) for t, v in rows]
    return equity_curve_svg(inverted, width=width, height=height).replace(
        "Account equity", "Drawdown from peak (%)"
    )


def promotion_gauge_svg(metrics: dict[str, Any], width: int = 580) -> str:
    """Horizontal pass/fail bars for paper promotion thresholds."""
    items: list[tuple[str, float, str, str]] = []
    checks = [
        ("Closed trades", metrics.get("pass_closed_trades"), metrics.get("closed_trades", 0), "int"),
        ("Win rate", metrics.get("pass_win_rate"), metrics.get("win_rate", 0), "pct"),
        ("Profit factor", metrics.get("pass_profit_factor"), metrics.get("profit_factor", 0), "num"),
        ("Max drawdown", metrics.get("pass_drawdown"), metrics.get("max_drawdown_pct", 0), "pct"),
    ]
    for label, passed, val, fmt in checks:
        bg = _C["buy_bg"] if passed else _C["sell_bg"]
        fg = _C["buy"] if passed else _C["sell"]
        if fmt == "int":
            display = str(int(val))
        elif fmt == "pct":
            display = f"{float(val):.1%}"
        else:
            display = f"{float(val):.2f}"
        items.append((f"{label} ({display})", 1.0, bg, fg))
    return hbar_chart_svg(items, "Paper promotion gate", width=width, unit="")


def pnl_by_exit_reason_svg(pnl_map: dict[str, float], width: int = 580) -> str:
    if not pnl_map:
        return _empty_chart("No exit P&L attribution yet", width, 120)
    items = []
    for k, v in sorted(pnl_map.items(), key=lambda x: abs(x[1]), reverse=True)[:10]:
        bg = _C["buy_bg"] if v >= 0 else _C["sell_bg"]
        fg = _C["buy"] if v >= 0 else _C["sell"]
        items.append((k.replace("_", " "), abs(v), bg, fg))
    return hbar_chart_svg(items, "P&L by exit reason (abs)", width=width, unit="$")


def _empty_chart(message: str, width: int, height: int) -> str:
    cx, cy = width // 2, height // 2
    return (
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg"'
        f' style="width:100%;max-width:{width}px;display:block">'
        f'<rect x="0" y="0" width="{width}" height="{height}" rx="8"'
        f' fill="{_C["bg"]}" stroke="{_C["border"]}" stroke-width="1"/>'
        f'<text x="{cx}" y="{cy + 5}" text-anchor="middle"'
        f' font-size="13" fill="{_C["muted"]}">{_esc(message)}</text>'
        f"</svg>"
    )
