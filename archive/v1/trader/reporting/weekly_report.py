"""Build weekly review context from JSONL + SQLite; write Markdown, JSON, LATEST, TRADER_FEEDBACK."""

from __future__ import annotations

import json
import os
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from trader.config import (
    DB_PATH,
    JSONL_PATH,
    MAX_OPTIONS_CONTRACTS_FLOOR,
    MAX_OPTIONS_CONTRACTS_HARD,
    OPTIONS_PROFIT_LADDER_DISABLE,
    OPTIONS_PROFIT_LADDER_FRACTION,
    OPTIONS_PROFIT_LADDER_PCT,
    OPTIONS_SIZE_TIERS,
    REPO_ROOT,
    TRADING_PROFILE,
    WATCHLIST_INCLUDE_OPEN_POSITIONS,
    WATCHLIST_SCREENER_SIZE,
)

_FEEDBACK_MARKER = "\n---FEEDBACK---\n"
_MAX_JSONL_FOR_PROMPT = int(os.getenv("WEEKLY_REVIEW_MAX_ENTRIES", "60"))
_MAX_JSON_CHARS = int(os.getenv("WEEKLY_REVIEW_MAX_JSON_CHARS", "95000"))
_HUMAN_JOURNAL_MAX = int(os.getenv("WEEKLY_HUMAN_JOURNAL_MAX_CHARS", "12000"))


def reports_dir() -> Path:
    p = Path(os.getenv("DATA_REPORTS_DIR", str(REPO_ROOT / "data" / "reports")))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _cutoff_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def load_jsonl_since(jsonl_path: Path, cutoff_iso: str) -> list[dict[str, Any]]:
    """Load entries newer than cutoff_iso from the journal and any rotated siblings.

    Walks rotated files (``journal.jsonl.YYYYMMDD-HHMMSS``) alongside the live file
    so weekly review continues to span the full window after a JOURNAL_MAX_BYTES
    rotation. Files are read oldest-first via mtime to preserve chronological order.
    """
    out: list[dict[str, Any]] = []
    candidates: list[Path] = []
    parent = jsonl_path.parent
    if parent.is_dir():
        # rotated siblings (e.g. journal.jsonl.20260512-153000) — oldest first
        try:
            rotated = sorted(
                (p for p in parent.glob(f"{jsonl_path.name}.*") if p.is_file()),
                key=lambda p: p.stat().st_mtime,
            )
            candidates.extend(rotated)
        except OSError:
            pass
    if jsonl_path.is_file():
        candidates.append(jsonl_path)
    for path in candidates:
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if (entry.get("timestamp") or "") > cutoff_iso:
                        out.append(entry)
        except OSError:
            continue
    return out


def slim_journal_entry(entry: dict[str, Any]) -> dict[str, Any]:
    logic = entry.get("logic")
    raw = None
    if isinstance(logic, dict):
        raw = logic.get("raw_decision")
    rd_action = raw.get("action") if isinstance(raw, dict) else None
    fd = logic.get("final_decision") if isinstance(logic, dict) else None
    fd_action = fd.get("action") if isinstance(fd, dict) else None
    ex = logic.get("execute") if isinstance(logic, dict) else None
    option_qty = None
    conviction_cap = None
    if isinstance(ex, dict):
        option_qty = ex.get("option_qty")
        conviction_cap = ex.get("conviction_contract_cap")
    ae = logic.get("automated_exit") if isinstance(logic, dict) else None
    ae_kind = ae.get("kind") if isinstance(ae, dict) else None
    return {
        "timestamp": entry.get("timestamp"),
        "symbol": entry.get("symbol"),
        "action": entry.get("action"),
        "strategy": entry.get("strategy"),
        "confidence": entry.get("confidence"),
        "execute_status": entry.get("execute_status"),
        "rationale": (entry.get("rationale") or "")[:240],
        "raw_model_action": rd_action,
        "final_action": fd_action,
        "option_qty": option_qty,
        "conviction_contract_cap": conviction_cap,
        "automated_exit_kind": ae_kind,
    }


def bot_operating_snapshot() -> dict[str, Any]:
    """Resolved sizing / watchlist / trail / ladder knobs at review time (for weekly prompts)."""
    tiers = [
        {"threshold": t.threshold, "max_contracts": t.max_contracts}
        for t in OPTIONS_SIZE_TIERS
    ]
    try:
        trail = float(os.getenv("TRAIL_GIVEBACK_PCT", "0.40") or 0.40)
    except ValueError:
        trail = 0.40
    ladder_enabled = (
        not OPTIONS_PROFIT_LADDER_DISABLE
        and bool(OPTIONS_PROFIT_LADDER_PCT)
        and len(OPTIONS_PROFIT_LADDER_PCT) == len(OPTIONS_PROFIT_LADDER_FRACTION)
    )
    return {
        "watchlist_screener_size": WATCHLIST_SCREENER_SIZE,
        "watchlist_include_open_positions": WATCHLIST_INCLUDE_OPEN_POSITIONS,
        "options_size_tiers": tiers,
        "max_options_contracts_floor": MAX_OPTIONS_CONTRACTS_FLOOR,
        "max_options_contracts_hard": MAX_OPTIONS_CONTRACTS_HARD,
        "trail_giveback_pct": trail,
        "options_profit_ladder": {
            "enabled": ladder_enabled,
            "pct_rungs": list(OPTIONS_PROFIT_LADDER_PCT),
            "cumulative_fractions": list(OPTIONS_PROFIT_LADDER_FRACTION),
        },
        "trading_profile": TRADING_PROFILE,
        "docs": "docs/MAGIC_NUMBERS.md",
    }


def exit_attribution_summary(days: int = 7) -> dict[str, Any]:
    """Position health & exit attribution for weekly review prompts."""
    from trader.research.eval_pack import build_eval_pack

    pack = build_eval_pack(days=days, profile=None)
    return {
        "exit_reason_counts": pack.get("exit_reason_counts") or {},
        "pnl_by_exit_reason": pack.get("pnl_by_exit_reason") or {},
        "recent_closed_trades": (pack.get("closed_trades") or [])[-15:],
        "promotion_metrics": pack.get("promotion_metrics") or {},
    }


def sqlite_stats_since(db_path: Path, cutoff_iso: str) -> dict[str, Any]:
    out: dict[str, Any] = {
        "rows_since_cutoff": 0,
        "by_action": {},
        "by_execute_status": {},
        "top_symbols": [],
        "equity_last": None,
        "equity_first": None,
    }
    if not db_path.is_file():
        return out
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        out["rows_since_cutoff"] = conn.execute(
            "SELECT COUNT(*) AS c FROM trades WHERE timestamp > ?",
            (cutoff_iso,),
        ).fetchone()["c"]
        for row in conn.execute(
            "SELECT action, COUNT(*) AS c FROM trades WHERE timestamp > ? GROUP BY action",
            (cutoff_iso,),
        ):
            out["by_action"][row["action"] or "NULL"] = row["c"]
        for row in conn.execute(
            "SELECT execute_status, COUNT(*) AS c FROM trades WHERE timestamp > ? GROUP BY execute_status",
            (cutoff_iso,),
        ):
            out["by_execute_status"][row["execute_status"] or "NULL"] = row["c"]
        sym_rows = conn.execute(
            """SELECT symbol, COUNT(*) AS c FROM trades WHERE timestamp > ?
               GROUP BY symbol ORDER BY c DESC LIMIT 15""",
            (cutoff_iso,),
        ).fetchall()
        out["top_symbols"] = [{"symbol": r["symbol"], "count": r["c"]} for r in sym_rows]
        last_eq = conn.execute(
            "SELECT equity FROM trades WHERE timestamp > ? AND equity IS NOT NULL ORDER BY id DESC LIMIT 1",
            (cutoff_iso,),
        ).fetchone()
        first_eq = conn.execute(
            "SELECT equity FROM trades WHERE timestamp > ? AND equity IS NOT NULL ORDER BY id ASC LIMIT 1",
            (cutoff_iso,),
        ).fetchone()
        if last_eq:
            out["equity_last"] = last_eq["equity"]
        if first_eq:
            out["equity_first"] = first_eq["equity"]
        conn.close()
    except sqlite3.Error:
        pass
    return out


def read_human_journal_tail() -> str | None:
    path_raw = (os.getenv("WEEKLY_HUMAN_JOURNAL_PATH") or "").strip()
    if not path_raw:
        return None
    p = Path(path_raw).expanduser()
    if not p.is_file():
        return None
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if len(text) > _HUMAN_JOURNAL_MAX:
        return "…\n\n" + text[-_HUMAN_JOURNAL_MAX:]
    return text


def fetch_alpaca_filled_summary(cutoff: datetime) -> dict[str, Any] | None:
    if os.getenv("WEEKLY_ALPACA_ORDERS_DISABLE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return None
    try:
        from alpaca.trading.enums import QueryOrderStatus
        from alpaca.trading.requests import GetOrdersRequest

        from trader.alpaca_runtime import trading_client

        tc = trading_client()
        req = GetOrdersRequest(
            status=QueryOrderStatus.CLOSED,
            after=cutoff,
            limit=200,
            nested=True,
        )
        orders = list(tc.get_orders(filter=req))
    except Exception:
        return None
    symbols: Counter[str] = Counter()
    for o in orders:
        sym = getattr(o, "symbol", None) or ""
        if sym:
            symbols[sym.upper()] += 1
    return {
        "filled_order_count": len(orders),
        "top_symbols": symbols.most_common(10),
    }


def build_weekly_context_pack(days: int = 7) -> dict[str, Any]:
    cutoff = _cutoff_iso(days)
    cutoff_dt = datetime.fromisoformat(cutoff.replace("Z", "+00:00"))
    raw_entries = load_jsonl_since(JSONL_PATH, cutoff)
    slim = [slim_journal_entry(e) for e in raw_entries[-_MAX_JSONL_FOR_PROMPT:]]
    stats = sqlite_stats_since(DB_PATH, cutoff)
    human = read_human_journal_tail()
    alpaca = fetch_alpaca_filled_summary(cutoff_dt)
    return {
        "period_days": days,
        "cutoff_utc": cutoff,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "jsonl_path": str(JSONL_PATH),
        "db_path": str(DB_PATH),
        "entries_in_window": len(raw_entries),
        "entries_for_prompt": slim,
        "sqlite_stats": stats,
        "human_build_journal_excerpt": human,
        "alpaca_filled_summary": alpaca,
        "bot_operating_snapshot": bot_operating_snapshot(),
        "exit_attribution": exit_attribution_summary(days=days),
    }


def _truncate_json_for_prompt(pack: dict[str, Any]) -> str:
    base = {k: v for k, v in pack.items() if k != "entries_for_prompt"}
    base["entries_for_prompt"] = pack["entries_for_prompt"]
    s = json.dumps(base, indent=2, default=str)
    while len(s) > _MAX_JSON_CHARS and len(base["entries_for_prompt"]) > 10:
        base["entries_for_prompt"] = base["entries_for_prompt"][:-5]
        s = json.dumps(base, indent=2, default=str)
    return s


def sonnet_prompt_from_pack(pack: dict[str, Any]) -> str:
    body = _truncate_json_for_prompt(pack)
    return f"""You are writing a **weekly internal blog post** (Markdown) for the operator of an automated trading bot.

Below is structured context: slim journal lines, SQLite aggregates, optional human build notes, optional Alpaca closed-order counts, and **bot_operating_snapshot** — the resolved env for watchlist screener cap, open-position injection, conviction-tiered option contract caps (floor / hard / tiers), and trail giveback %. Use it when interpreting option BUY sizes (`option_qty`, `conviction_contract_cap` on journal lines) and cycle breadth.

CONTEXT JSON:
{body}

Write **Markdown** suitable for publishing with these sections (use ## headings):
1. ## TL;DR
2. ## What changed (use human_build_journal_excerpt if present; otherwise say not provided)
3. ## Trading behavior (reference entries_for_prompt and sqlite_stats; do not invent fills; tie multi-contract option opens to bot_operating_snapshot when those fields appear; **automated_exit_kind** / rationale suffix *_automation mark rule-based SELLs — trail giveback, profit ladder, expiry sweep, **bracket_take_profit / bracket_stop** (GTC bracket legs filled at Alpaca) — not model decisions)
4. ## Risks and biases you notice
5. ## Next week (three concrete, actionable improvements)

Rules:
- Be direct. If data is thin, say so.
- Do not invent dollar PnL; you may compare equity_first vs equity_last from sqlite_stats if both are numbers.
- End your response with the exact delimiter line below, then a short **bullet list** (max 1200 characters) of distilled lessons for the *trading model* to remember next week (not live market facts). This section is extracted into TRADER_FEEDBACK.md.

---FEEDBACK---
- (bullets here)
"""


def split_sonnet_blog_and_feedback(full_text: str) -> tuple[str, str]:
    if _FEEDBACK_MARKER in full_text:
        blog, fb = full_text.split(_FEEDBACK_MARKER, 1)
        return blog.strip(), fb.strip()[:2000]
    return full_text.strip(), ""


def default_feedback_from_stats(pack: dict[str, Any]) -> str:
    stats = pack.get("sqlite_stats") or {}
    lines = [
        "# Trader feedback (auto-generated)",
        "",
        f"- Journal rows in window: {pack.get('entries_in_window', 0)}",
        f"- SQLite rows: {stats.get('rows_since_cutoff', 0)}",
        f"- By action: {stats.get('by_action')}",
        f"- By execute_status: {stats.get('by_execute_status')}",
        "",
        "Review the weekly blog post for qualitative lessons.",
    ]
    return "\n".join(lines)[:2000]


def write_weekly_artifacts(
    pack: dict[str, Any],
    blog_markdown: str,
    feedback_markdown: str,
    sonnet_model: str,
) -> dict[str, str]:
    rd = reports_dir()
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fb = feedback_markdown.strip() or default_feedback_from_stats(pack)
    meta = {
        "period_days": pack.get("period_days"),
        "cutoff_utc": pack.get("cutoff_utc"),
        "generated_at_utc": pack.get("generated_at_utc"),
        "sqlite_stats": pack.get("sqlite_stats"),
        "entries_in_window": pack.get("entries_in_window"),
        "alpaca_filled_summary": pack.get("alpaca_filled_summary"),
        "bot_operating_snapshot": pack.get("bot_operating_snapshot"),
        "sonnet_model": sonnet_model,
    }
    paths = {
        "weekly_md": str(rd / f"weekly-{day}.md"),
        "weekly_json": str(rd / f"weekly-{day}.json"),
        "latest_md": str(rd / "LATEST_REVIEW.md"),
        "feedback_md": str(rd / "TRADER_FEEDBACK.md"),
    }
    (rd / f"weekly-{day}.md").write_text(blog_markdown, encoding="utf-8")
    (rd / f"weekly-{day}.json").write_text(
        json.dumps(meta, indent=2, default=str),
        encoding="utf-8",
    )
    (rd / "LATEST_REVIEW.md").write_text(blog_markdown, encoding="utf-8")
    (rd / "TRADER_FEEDBACK.md").write_text(fb, encoding="utf-8")
    return paths
