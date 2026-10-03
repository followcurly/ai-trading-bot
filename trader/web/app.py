"""FastAPI read-only viewer for journal / logic_json."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from trader.alpaca_runtime import trading_client
from trader.config import TRADE_LOG_VIEW_TOKEN, TRADING_PROFILE
from trader.db import connect as _db_connect
from trader.market_hours import is_us_equity_rth
from trader.web.blog_config import BlogConfig

_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _DIR.parent.parent
_DEFAULT_ARCHITECTURE_MD = _REPO_ROOT / "docs" / "ARCHITECTURE.md"
templates = Jinja2Templates(directory=str(_DIR / "templates"))

# Make the blog chrome available to every template (incl. base.html nav).
# Read at import time; restart the service to pick up env changes.
templates.env.globals["blog"] = BlogConfig.from_env().to_template_context()


def human_timestamp(iso: str | None) -> str:
    """Format stored journal ISO timestamps for the UI (Eastern + UTC)."""
    if iso is None:
        return "—"
    s = str(iso).strip()
    if not s:
        return "—"
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
    except ValueError:
        return s
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    utc = dt.astimezone(timezone.utc)
    utc_clock = utc.strftime("%H:%M UTC")
    try:
        et = dt.astimezone(ZoneInfo("America/New_York"))
        et_head = f"{et.strftime('%b')} {et.day}, {et.strftime('%Y · %I:%M %p %Z')}"
        return f"{et_head} ({utc_clock})"
    except Exception:
        return f"{utc.strftime('%b')} {utc.day}, {utc.year} · {utc_clock}"


templates.env.filters["human_timestamp"] = human_timestamp


def _journal_cycle_stale_rth(last_ts_iso: str | None) -> bool:
    """True when the latest journal row is older than 90 minutes during US RTH."""
    if not last_ts_iso:
        return False
    if not is_us_equity_rth(datetime.now(ZoneInfo("America/New_York"))):
        return False
    s = str(last_ts_iso).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
    return age > timedelta(minutes=90)


def _smoke_render_templates() -> None:
    """Fail fast at import time if any Jinja template breaks (systemd will log + restart)."""
    if os.getenv("PYTEST_CURRENT_TEST"):
        return
    raw = os.getenv("TRADE_LOG_TEMPLATE_SMOKE", "true").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return
    log = logging.getLogger("trader.web")
    req = type("Req", (), {"url": "/?token=smoke"})()
    stub: dict[str, Any] = {
        "request": req,
        "page_title": "Smoke",
        "query_token": "smoke",
        "architecture_html": "<p>ok</p>",
        "total_cycles": 0,
        "last_cycle_time": None,
        "scheduler_cadence_line": "test",
        "rows": [],
        "symbols": [],
        "symbol_filter": "",
        "limit": 100,
        "live_account": None,
        "cycle_stale_rth": False,
        "positions": [],
        "positions_error": None,
        "account_strip": None,
        # tax.html
        "form": {
            "entity": "individual_single",
            "ordinary_income": 0.0,
            "short_term_gains": 0.0,
            "qualified_dividends": 0.0,
            "long_term_gains": 0.0,
            "other_investment_income": 0.0,
            "deduction": None,
            "run_id": "",
        },
        "result": None,
        "error": None,
        "entity_label": None,
        "prefill_note": None,
        # backtest_compare.html
        "payload": {"runs": [], "equity": [], "trades": [], "metrics": {}},
        "row": {
            "id": 1,
            "timestamp": "2026-01-01T12:00:00+00:00",
            "symbol": "SPY",
            "action": "HOLD",
            "strategy": "long_stock",
            "rationale": "—",
            "equity": 100000.0,
            "daily_pnl": 0.0,
            "logic_json": None,
        },
        "logic_pretty": None,
        "snapshot_excerpt_pretty": None,
        "raw_decision_pretty": None,
        "final_decision_pretty": None,
        "execute_pretty": None,
        "model_confidence": None,
        "post_reason": None,
        "exec_status": None,
        "exec_error": None,
        "exec_order_id": None,
        "pipeline": None,
        "display_rationale": "—",

        # backtest templates
        "runs": [],
        "default_start": "2025-01-01",
        "default_end": "2026-01-01",
        "default_cash": 100000,
        "error": None,
        "run": {
            "id": "smoke",
            "created_at": "2026-01-01T00:00:00+00:00",
            "start_date": "2025-01-01",
            "end_date": "2026-01-01",
            "cash": 100000.0,
            "cost_bps": 5.0,
            "fill_rule": "next_open",
            "status": "ok",
            "metrics": {
                "full": {
                    "total_return": 0.0,
                    "vs_spy": 0.0,
                    "max_drawdown": 0.0,
                    "cagr": 0.0,
                },
                "is": {"total_return": 0.0, "vs_spy": 0.0},
                "oos": {"total_return": 0.0, "vs_spy": 0.0},
                "split_date": "2025-09-01",
                "trade_count": 0,
            },
            "trades": [],
            "equity": [],
        },
        "payload": {"equity": [], "trades": [], "metrics": {}},

        # blog templates
        "posts": [],
        "post": {
            "slug": "smoke",
            "title": "Smoke test",
            "date": "2026-01-01",
            "excerpt": "smoke",
            "html_body": "<p>ok</p>",
        },
        "prev_slug": None,
        "next_slug": None,
        "charts": {},
        "profile": "default",
        "days": 30,
        "trading_profile": "default",
        "promotion": {},
        "thresholds": {},
        "closed_trades": [],
        "narrative": {
            "headline": "Smoke",
            "bullets": [],
            "warnings": [],
            "action_glossary": {},
            "exit_glossary": {},
        },
        "action_glossary": {},
        "exit_glossary": {},
        "by_action": {},
        "by_execute_status": {},
        "exit_reason_counts": {},
        "summary": {
            "version": 1,
            "title": "v1 trading experiment",
            "retired": "2026-07-30",
            "why": "smoke",
            "empty": True,
            "total_cycles": 0,
            "unique_symbols": 0,
            "orders_placed": 0,
            "orders_errored": 0,
            "period": {"start": None, "end": None},
            "equity": {
                "start": 100000.0,
                "end": 100000.0,
                "peak": 100000.0,
                "floor": 100000.0,
                "pnl": 0.0,
                "pnl_pct": 0.0,
                "max_drawdown": 0.0,
                "max_drawdown_pct": 0.0,
            },
            "actions": [],
            "execute_status": [],
            "buy_strategies": [],
            "profiles": [],
            "top_symbols_placed": [],
            "artifacts": {
                "db": "data/archive/v1/trades.db",
                "jsonl": "data/archive/v1/journal.jsonl",
                "code": "archive/v1/",
                "tarball": "/srv/archive/ai-trading-bot-v1-20260730.tar.gz",
            },
        },
    }
    failures: list[tuple[str, str]] = []
    for name in sorted(templates.env.list_templates()):
        if not str(name).endswith(".html"):
            continue
        try:
            templates.env.get_template(name).render(**stub)
        except Exception as e:
            failures.append((name, f"{type(e).__name__}: {e}"))
    if failures:
        for n, err in failures:
            log.error("template_smoke_fail template=%s error=%s", n, err)
        sys.exit(1)
    n_html = sum(1 for n in templates.env.list_templates() if str(n).endswith(".html"))
    log.info("template_smoke_ok html_templates=%d", n_html)


_smoke_render_templates()


def _architecture_md_path() -> Path:
    """Path to the Markdown source; override with ARCHITECTURE_DOC_PATH for odd layouts."""
    raw = (os.getenv("ARCHITECTURE_DOC_PATH") or "").strip()
    if raw:
        return Path(raw).expanduser()
    return _DEFAULT_ARCHITECTURE_MD

app = FastAPI(title="Trade logic", docs_url=None, redoc_url=None)

_STATIC_DIR = _DIR / "static"
if _STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.middleware("http")
async def _token_guard(request: Request, call_next):
    if not TRADE_LOG_VIEW_TOKEN:
        return await call_next(request)
    if request.query_params.get("token") != TRADE_LOG_VIEW_TOKEN:
        return PlainTextResponse(
            "Unauthorized: pass query ?token= matching TRADE_LOG_VIEW_TOKEN in api.env\n",
            status_code=401,
        )
    return await call_next(request)


def _conn() -> sqlite3.Connection:
    return _db_connect(row_factory=sqlite3.Row)


def _parse_logic(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"parse_error": True, "raw": raw[:2000]}


def _fmt_int_or_float(v: Any) -> str | None:
    """Render `2.0 → '2'`, `2.5 → '2.5'`, anything non-numeric → None."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f"{int(f)}" if f.is_integer() else f"{f:.2f}".rstrip("0").rstrip(".")


def _fmt_signed_pct(v: Any) -> str | None:
    """`unrealized_pct` is stored as a fraction (1.0 = +100%). Render as `+312%`."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    pct = f * 100.0
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.0f}%"


def _fmt_money(v: Any) -> str | None:
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return None


def _enrich_automation(kind: str, ae: dict, execute: dict | None) -> str | None:
    """Build a single-line, operator-readable summary for a rule-based exit.

    `ae` is `logic_json.automated_exit`; `execute` is `logic_json.execute`.
    Returns None if the kind is unknown so callers can fall back to the raw
    rationale string.
    """
    if kind == "profit_ladder":
        rung = ae.get("rung")
        thresh = ae.get("rung_threshold_pct")
        unreal = ae.get("unrealized_pct")
        qty = ae.get("qty_closed")
        occ = ae.get("occ_symbol")
        parts = ["Profit ladder"]
        if rung is not None:
            try:
                parts.append(f"rung {int(rung) + 1}")
            except (TypeError, ValueError):
                pass
        if thresh is not None:
            parts.append(f"(≥+{_fmt_int_or_float(thresh) or thresh}% gain)")
        qty_s = _fmt_int_or_float(qty)
        if qty_s:
            parts.append(f"— closed {qty_s} contract" + ("s" if qty_s != "1" else ""))
        if occ:
            parts.append(f"of {occ}")
        unreal_s = _fmt_signed_pct(unreal)
        if unreal_s is not None:
            parts.append(f"at {unreal_s}")
        return " ".join(parts)

    if kind == "trail_giveback":
        peak = ae.get("peak_unrealized_pnl")
        now = ae.get("unrealized_pnl_at_exit")
        floor = ae.get("trail_floor")
        give = ae.get("trail_giveback_pct")
        occ = ae.get("occ_symbol")
        parts = ["Trail giveback"]
        if occ:
            parts.append(f"({occ})")
        peak_s = _fmt_money(peak)
        now_s = _fmt_money(now)
        if peak_s and now_s:
            parts.append(f"— peak P&L {peak_s}, now {now_s}")
        elif peak_s:
            parts.append(f"— peak P&L {peak_s}")
        if floor is not None:
            floor_s = _fmt_money(floor)
            if floor_s:
                parts.append(f"(floor {floor_s})")
        give_s = _fmt_int_or_float(
            (give * 100.0) if isinstance(give, (int, float)) else None
        )
        if give_s:
            parts.append(f"· >{give_s}% giveback")
        return " ".join(parts)

    if kind == "expiry_sweep":
        occ = ae.get("occ_symbol")
        expiry = ae.get("expiry_iso")
        qty = ae.get("qty_closed")
        parts = ["Expiry sweep — closed"]
        qty_s = _fmt_int_or_float(qty)
        if qty_s:
            parts.append(f"{qty_s} contract" + ("s" if qty_s != "1" else ""))
        else:
            parts.append("position")
        if occ:
            parts.append(f"of {occ}")
        if expiry:
            parts.append(f"expiring {expiry}")
        return " ".join(parts)

    if kind in ("bracket_take_profit", "bracket_stop"):
        label = "Bracket take-profit" if kind == "bracket_take_profit" else "Bracket stop"
        qty = (execute or {}).get("filled_qty")
        avg = (execute or {}).get("filled_avg_price")
        ot = ae.get("alpaca_order_type")
        parts = [f"{label} fill"]
        qty_s = _fmt_int_or_float(qty)
        avg_s = _fmt_money(avg)
        if qty_s and avg_s:
            parts.append(f"— {qty_s} share" + ("s" if qty_s != "1" else "") + f" @ {avg_s}")
        elif qty_s:
            parts.append(f"— {qty_s} share" + ("s" if qty_s != "1" else ""))
        if ot:
            parts.append(f"({ot})")
        return " ".join(parts)

    return None


def enrich_rationale(rationale: Any, logic: Any) -> str:
    """Return a richer human-readable rationale by combining the raw rationale
    string with the structured `logic_json` payload (automated_exit).

    Falls back to the raw rationale on any missing/unexpected data so the UI
    is never worse off than today. Always returns a non-empty string.
    """
    base = (str(rationale).strip() if rationale not in (None, "") else "") or "—"
    if not isinstance(logic, dict):
        return base

    ae = logic.get("automated_exit")
    if isinstance(ae, dict):
        kind = ae.get("kind") or ""
        execute = logic.get("execute") if isinstance(logic.get("execute"), dict) else None
        rich = _enrich_automation(kind, ae, execute)
        if rich:
            return rich

    return base


templates.env.filters["enrich_rationale"] = enrich_rationale


def _default_limit(raw: str | None) -> int:
    try:
        n = int(raw or "100")
    except ValueError:
        n = 100
    return max(1, min(n, 500))


def _col_exists(conn: sqlite3.Connection, table: str, col: str) -> bool:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    return col in cols


def _pipeline_chip(state: str) -> str:
    return {
        "ok": "not_attempted",
        "empty": "not_attempted",
        "placed": "placed",
        "skipped": "skipped",
        "error": "error",
        "na": "not_attempted",
        "hold": "hold",
        "halt": "halt",
    }.get(state, "not_attempted")


def build_pipeline(logic: Any, row: dict[str, Any]) -> dict[str, Any] | None:
    """Stages for cycle detail UI (from logic_json + row columns)."""
    stages: list[dict[str, str]] = []
    snap_ex = logic.get("snapshot_excerpt") if isinstance(logic, dict) else None
    has_snap = bool(snap_ex)
    stages.append(
        {
            "key": "snapshot",
            "title": "Snapshot",
            "state": "ok" if has_snap else "empty",
            "detail": "excerpt stored" if has_snap else "no excerpt",
            "chip": _pipeline_chip("ok" if has_snap else "empty"),
        }
    )

    raw_dec = logic.get("raw_decision") if isinstance(logic, dict) else None
    final_dec = logic.get("final_decision") if isinstance(logic, dict) else None
    exe = logic.get("execute") if isinstance(logic, dict) else None

    if isinstance(raw_dec, dict):
        cf = raw_dec.get("confidence")
        ac = raw_dec.get("action")
        det = f"model {ac or '?'}"
        if cf is not None:
            det += f", conf {cf}"
        stages.append(
            {
                "key": "brain",
                "title": "Brain",
                "state": "ok",
                "detail": det[:120],
                "chip": _pipeline_chip("ok"),
            }
        )
    else:
        stages.append(
            {
                "key": "brain",
                "title": "Brain",
                "state": "empty",
                "detail": "—",
                "chip": _pipeline_chip("empty"),
            }
        )

    fin_action = final_dec.get("action") if isinstance(final_dec, dict) else None
    raw_action = raw_dec.get("action") if isinstance(raw_dec, dict) else None
    reason = (final_dec.get("reason") if isinstance(final_dec, dict) else None) or ""
    if fin_action in ("HOLD", "HALT"):
        st = str(fin_action).lower()
        stages.append(
            {
                "key": "risk",
                "title": "Risk",
                "state": st,
                "detail": (reason or "blocked")[:120],
                "chip": _pipeline_chip(st),
            }
        )
    elif isinstance(final_dec, dict):
        det = (
            "passed"
            if raw_action == fin_action
            else f"{raw_action or '?'} → {fin_action or '?'}"
        )
        stages.append(
            {
                "key": "risk",
                "title": "Risk",
                "state": "ok",
                "detail": det[:120],
                "chip": _pipeline_chip("ok"),
            }
        )
    else:
        stages.append(
            {
                "key": "risk",
                "title": "Risk",
                "state": "empty",
                "detail": "—",
                "chip": _pipeline_chip("empty"),
            }
        )

    exec_status = (row.get("execute_status") or "").strip() or None
    ex_err = (row.get("execute_error") or "").strip()
    if fin_action not in ("BUY", "SELL"):
        det = exec_status or "not_attempted"
        stages.append(
            {
                "key": "execute",
                "title": "Execute",
                "state": "na",
                "detail": det,
                "chip": _pipeline_chip("na"),
            }
        )
    elif exec_status == "placed":
        oid = row.get("order_id") or (
            exe.get("order_id") if isinstance(exe, dict) else None
        )
        stages.append(
            {
                "key": "execute",
                "title": "Execute",
                "state": "placed",
                "detail": f"order_id {oid or '—'}",
                "chip": "placed",
            }
        )
    elif exec_status == "error":
        msg = ex_err or (
            (exe.get("reason") if isinstance(exe, dict) else None) or "error"
        )
        stages.append(
            {
                "key": "execute",
                "title": "Execute",
                "state": "error",
                "detail": str(msg)[:120],
                "chip": "error",
            }
        )
    elif exec_status == "skipped":
        msg = ex_err or (
            (exe.get("reason") if isinstance(exe, dict) else None) or "skipped"
        )
        stages.append(
            {
                "key": "execute",
                "title": "Execute",
                "state": "skipped",
                "detail": str(msg)[:120],
                "chip": "skipped",
            }
        )
    else:
        stages.append(
            {
                "key": "execute",
                "title": "Execute",
                "state": exec_status or "unknown",
                "detail": "—",
                "chip": _pipeline_chip(exec_status or "na"),
            }
        )

    return {"stages": stages}


@app.get("/healthz")
def healthz() -> Any:
    try:
        with _conn() as conn:
            n = conn.execute("SELECT COUNT(*) AS c FROM trades").fetchone()["c"]
        return JSONResponse({"ok": True, "rows": int(n)})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=503)


def _v1_summary_context(request: Request) -> dict[str, Any]:
    """Shared context for frozen v1 summary page."""
    from trader.v1_summary import build_v1_summary

    qtok = request.query_params.get("token") or ""
    archive_summary = _REPO_ROOT / "data" / "archive" / "v1" / "summary.json"
    archive_db = _REPO_ROOT / "data" / "archive" / "v1" / "trades.db"
    summary: dict[str, Any]
    if archive_summary.is_file():
        try:
            summary = json.loads(archive_summary.read_text(encoding="utf-8"))
        except Exception:
            summary = (
                build_v1_summary(archive_db)
                if archive_db.is_file()
                else {"empty": True, "retired": "2026-07-30", "title": "v1"}
            )
    elif archive_db.is_file():
        summary = build_v1_summary(archive_db)
    else:
        summary = {
            "version": 1,
            "title": "v1 trading experiment",
            "retired": "2026-07-30",
            "empty": True,
            "total_cycles": 0,
        }

    live_acct: dict[str, Any] | None = None
    try:
        tc = trading_client()
        a = tc.get_account()
        live_acct = {
            "equity": float(a.equity),
            "cash": float(a.cash),
            "open_positions": len(tc.get_all_positions()),
        }
    except Exception:
        live_acct = None

    return {
        "query_token": qtok,
        "page_title": "v1 experiment — closed",
        "summary": summary,
        "live_account": live_acct,
    }


@app.get("/architecture/v1", response_class=HTMLResponse)
def architecture_v1_page(request: Request) -> Any:
    """Frozen v1 experiment summary (subpage of Architecture)."""
    return templates.TemplateResponse(request, "v1.html", _v1_summary_context(request))


@app.get("/v1")
def v1_redirect(request: Request) -> RedirectResponse:
    q = request.url.query
    loc = "/architecture/v1" + (f"?{q}" if q else "")
    return RedirectResponse(loc, status_code=307)


@app.get("/report/latest.md")
def latest_weekly_report() -> PlainTextResponse:
    """v1 weekly Sonnet review retired — serve static file if present."""
    p = _REPO_ROOT / "data" / "reports" / "LATEST_REVIEW.md"
    if not p.is_file():
        raise HTTPException(
            status_code=404,
            detail="v1 weekly review retired; no LATEST_REVIEW.md on disk.",
        )
    return PlainTextResponse(
        p.read_text(encoding="utf-8"),
        media_type="text/markdown; charset=utf-8",
    )


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> Any:
    qtok = request.query_params.get("token") or ""
    symbol_filter = (request.query_params.get("symbol") or "").strip().upper() or None
    limit = _default_limit(request.query_params.get("limit"))

    with _conn() as conn:
        # Guard for old DBs that haven't been migrated yet by init_db
        has_exec_cols = _col_exists(conn, "trades", "execute_status")
        exec_select = ", execute_status, order_id, execute_error" if has_exec_cols else ""

        symbols = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT symbol FROM trades WHERE symbol IS NOT NULL AND symbol != '' ORDER BY symbol"
            ).fetchall()
        ]
        params: list[Any] = []
        where_clause = ""
        if symbol_filter:
            where_clause = "WHERE symbol = ?"
            params.append(symbol_filter)
        params.append(limit)
        sql = f"""SELECT id, timestamp, symbol, action, strategy, confidence,
                         rationale, equity, daily_pnl, logic_json{exec_select}
                  FROM trades {where_clause}
                  ORDER BY id DESC LIMIT ?"""
        rows = conn.execute(sql, params).fetchall()

    last_cycle_time = rows[0]["timestamp"] if rows else None
    title = "Trade logic — cycles"
    if symbol_filter:
        title = f"Trade logic — {symbol_filter}"

    live_acct: dict[str, Any] | None = None
    try:
        acc = trading_client().get_account()
        live_acct = {
            "equity": float(acc.equity),
            "daytrade_count": int(getattr(acc, "daytrade_count", 0) or 0),
            "pattern_day_trader": bool(getattr(acc, "pattern_day_trader", False)),
        }
    except Exception:
        live_acct = None

    rendered_rows: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        logic = _parse_logic(d.pop("logic_json", None))
        d["display_rationale"] = enrich_rationale(d.get("rationale"), logic)
        rendered_rows.append(d)

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "rows": rendered_rows,
            "query_token": qtok,
            "symbols": symbols,
            "symbol_filter": symbol_filter or "",
            "limit": limit,
            "last_cycle_time": last_cycle_time,
            "page_title": title,
            "live_account": live_acct,
            "cycle_stale_rth": _journal_cycle_stale_rth(
                str(last_cycle_time) if last_cycle_time else None
            ),
            "trading_profile": TRADING_PROFILE,
        },
    )


@app.get("/research")
def research_redirect(request: Request) -> RedirectResponse:
    q = request.url.query
    loc = "/backtest" + (f"?{q}" if q else "")
    return RedirectResponse(loc, status_code=307)


# --- backtest sidecar routes ---
@app.get("/backtest", response_class=HTMLResponse)
def backtest_home(request: Request) -> Any:
    from datetime import date as _date

    from trader.backtest.store import list_runs

    qtok = request.query_params.get("token") or ""
    today = _date.today()
    start = today.replace(year=today.year - 1)
    return templates.TemplateResponse(
        request,
        "backtest.html",
        {
            "request": request,
            "page_title": "Backtest lab",
            "query_token": qtok,
            "runs": list_runs(50),
            "default_start": start.isoformat(),
            "default_end": today.isoformat(),
            "default_cash": 100000,
            "error": request.query_params.get("error") or None,
        },
    )


@app.post("/backtest/run")
async def backtest_run(request: Request) -> Any:
    from datetime import date as _date
    from urllib.parse import quote

    from trader.backtest.engine import (
        MODES,
        UNIVERSES,
        BacktestParams,
        run_backtest,
        run_lookback_compare,
        run_strategy_compare,
        run_universe_compare,
    )

    qtok = request.query_params.get("token") or ""
    form = await request.form()
    token = str(form.get("token") or qtok or "")

    def _redir_err(msg: str) -> RedirectResponse:
        loc = f"/backtest?token={quote(token)}&error={quote(msg)}"
        return RedirectResponse(loc, status_code=303)

    def _truthy(raw: Any) -> bool:
        return str(raw or "").strip().lower() in ("1", "on", "true", "yes")

    try:
        start = _date.fromisoformat(str(form.get("start") or "").strip())
        end = _date.fromisoformat(str(form.get("end") or "").strip())
        cash = float(form.get("cash") or 100000)
        cost_bps = float(form.get("cost_bps") or 5)
        fill_rule = str(form.get("fill_rule") or "next_open").strip()
        if fill_rule not in ("next_open", "same_close"):
            fill_rule = "next_open"

        mode = str(form.get("mode") or "buy_hold").strip().lower()
        if mode == "year_end_skim":
            mode = "buy_hold"
        if mode not in MODES:
            mode = "buy_hold"

        universe = str(form.get("universe") or "balanced").strip().lower()
        if universe not in UNIVERSES:
            universe = "balanced"

        dca_cadence = str(form.get("dca_cadence") or "monthly").strip().lower()
        if dca_cadence not in ("weekly", "monthly"):
            dca_cadence = "monthly"
        try:
            dca_months = int(form.get("dca_months") or 6)
        except (TypeError, ValueError):
            dca_months = 6

        try:
            red_lookback = int(form.get("red_lookback") or 1)
        except (TypeError, ValueError):
            red_lookback = 1
        if red_lookback not in (1, 2, 3):
            red_lookback = 1

        try:
            min_consecutive_reds = int(form.get("min_consecutive_reds") or 1)
        except (TypeError, ValueError):
            min_consecutive_reds = 1
        if min_consecutive_reds not in (1, 2, 3):
            min_consecutive_reds = 1

        raw_down = float(form.get("min_down_pct") or 0)
        min_down_pct = raw_down / 100.0 if raw_down > 1 else raw_down

        ma_raw = str(form.get("ma_filter") or "").strip().lower()
        ma_filter: int | None = None
        if ma_raw and ma_raw not in ("0", "none", ""):
            try:
                ma_filter = int(ma_raw)
            except (TypeError, ValueError):
                ma_filter = None
            if ma_filter not in (50, 100, 200):
                ma_filter = None

        year_end_skim = _truthy(form.get("year_end_skim"))
        try:
            skim_pct = float(form.get("skim_pct") or 0.10)
        except (TypeError, ValueError):
            skim_pct = 0.10
        skim_gate = str(form.get("skim_gate") or "always").strip().lower()
        if skim_gate not in ("always", "gain"):
            skim_gate = "always"

        params = BacktestParams(
            start=start,
            end=end,
            cash=cash,
            cost_bps=cost_bps,
            fill_rule=fill_rule,
            mode=mode,
            red_lookback=red_lookback,
            min_consecutive_reds=min_consecutive_reds,
            min_down_pct=min_down_pct,
            ma_filter=ma_filter,
            dca_cadence=dca_cadence,
            dca_months=dca_months,
            universe=universe,
            year_end_skim=year_end_skim,
            skim_pct=skim_pct,
            skim_gate=skim_gate,
        )

        compare_kind = str(form.get("compare_kind") or "").strip().lower()
        compare_values = [
            v.strip()
            for v in str(form.get("compare_values") or "").split(",")
            if v.strip()
        ]
        # Legacy checkbox: compare all three lookbacks
        if not compare_kind and _truthy(form.get("compare_lookbacks")):
            compare_kind = "lookback"
            compare_values = ["1", "2", "3"]

        if compare_kind and len(compare_values) >= 2:
            if compare_kind == "strategy":
                out = run_strategy_compare(params, modes=compare_values)
            elif compare_kind == "universe":
                out = run_universe_compare(params, universes=compare_values)
            elif compare_kind == "lookback":
                lookbacks = []
                for v in compare_values:
                    try:
                        lookbacks.append(int(v))
                    except (TypeError, ValueError):
                        continue
                out = run_lookback_compare(params, lookbacks=lookbacks)
            else:
                return _redir_err(
                    f"Unknown compare_kind {compare_kind!r}; "
                    "use strategy, universe, or lookback"
                )
            ids = ",".join(out["ids"])
            kind_q = quote(compare_kind)
            loc = (
                f"/backtest/compare?ids={quote(ids)}"
                f"&kind={kind_q}&token={quote(token)}"
            )
            return RedirectResponse(loc, status_code=303)

        result = run_backtest(params, persist=True)
    except Exception as e:
        return _redir_err(str(e)[:400])

    loc = f"/backtest/{result['id']}?token={quote(token)}"
    return RedirectResponse(loc, status_code=303)


def _backtest_run_label(run: dict[str, Any], *, compare_kind: str | None = None) -> str:
    """Human label for a saved run on the compare page / chart legend."""
    mode = str(run.get("mode") or (run.get("params") or {}).get("mode") or "buy_hold")
    if mode == "year_end_skim":
        mode = "buy_hold"
    skim = bool(run.get("year_end_skim")) or bool(
        (run.get("params") or {}).get("year_end_skim")
    )
    universe = str(
        run.get("universe") or (run.get("params") or {}).get("universe") or "balanced"
    )
    lb = int(
        run.get("red_lookback")
        or (run.get("params") or {}).get("red_lookback")
        or 1
    )
    mode_labels = {
        "lump_sum": "Lump sum",
        "dca": "DCA",
        "buy_hold": "Buy the dip",
    }
    plan = mode_labels.get(mode, mode)
    if skim:
        plan = f"{plan} +skim"

    if compare_kind == "universe":
        return universe
    if compare_kind == "lookback":
        return f"{lb}d red"
    if compare_kind == "strategy":
        return plan
    # Mixed / unknown: include the distinctive bits
    if mode == "buy_hold":
        return f"{plan} · {lb}d · {universe}"
    return f"{plan} · {universe}"


@app.get("/backtest/compare", response_class=HTMLResponse)
def backtest_compare(request: Request) -> Any:
    from trader.backtest.store import load_run

    qtok = request.query_params.get("token") or ""
    kind = (request.query_params.get("kind") or "").strip().lower() or None
    ids_raw = (request.query_params.get("ids") or "").strip()
    ids = [x.strip() for x in ids_raw.split(",") if x.strip()]
    runs = []
    for rid in ids:
        r = load_run(rid)
        if r:
            runs.append(r)

    # Infer compare kind from run diversity when not passed
    if not kind and runs:
        modes = {
            str(r.get("mode") or (r.get("params") or {}).get("mode") or "buy_hold")
            for r in runs
        }
        unis = {
            str(r.get("universe") or (r.get("params") or {}).get("universe") or "balanced")
            for r in runs
        }
        lbs = {
            int(
                r.get("red_lookback")
                or (r.get("params") or {}).get("red_lookback")
                or 1
            )
            for r in runs
        }
        if len(modes) > 1:
            kind = "strategy"
        elif len(unis) > 1:
            kind = "universe"
        elif len(lbs) > 1:
            kind = "lookback"

    title_by_kind = {
        "strategy": "Plan compare",
        "universe": "ETF mix compare",
        "lookback": "Lookback compare",
    }
    title = title_by_kind.get(kind or "", "Compare")

    rows = []
    best_ret = None
    for r in runs:
        m = r.get("metrics") or {}
        full = m.get("full") or {}
        oos = m.get("oos") or {}
        ret = full.get("total_return")
        mode = str(r.get("mode") or (r.get("params") or {}).get("mode") or "buy_hold")
        if mode == "year_end_skim":
            mode = "buy_hold"
        universe = str(
            r.get("universe") or (r.get("params") or {}).get("universe") or "balanced"
        )
        lb = int(
            r.get("red_lookback")
            or (r.get("params") or {}).get("red_lookback")
            or 1
        )
        row = {
            "id": r["id"],
            "label": _backtest_run_label(r, compare_kind=kind),
            "mode": mode,
            "universe": universe,
            "lookback": lb,
            "total_return": ret,
            "vs_spy": full.get("vs_spy"),
            "max_drawdown": full.get("max_drawdown"),
            "cagr": full.get("cagr"),
            "trade_count": m.get("trade_count") or 0,
            "dividend_cash": (m.get("dividends") or {}).get("total_cash"),
            "oos_return": oos.get("total_return"),
            "best_return": False,
        }
        rows.append(row)
        if ret is not None and (best_ret is None or ret > best_ret):
            best_ret = ret
    for row in rows:
        if best_ret is not None and row["total_return"] == best_ret:
            row["best_return"] = True

    if kind == "lookback":
        rows.sort(key=lambda x: x["lookback"])
    elif kind == "universe":
        rows.sort(key=lambda x: x["universe"])
    else:
        rows.sort(key=lambda x: x["label"])

    meta = None
    if runs:
        r0 = runs[0]
        meta = {
            "start_date": r0["start_date"],
            "end_date": r0["end_date"],
            "cash": r0["cash"],
            "fill_rule": r0["fill_rule"],
            "cost_bps": r0["cost_bps"],
            "universe": r0.get("universe")
            or (r0.get("params") or {}).get("universe")
            or "balanced",
            "mode": r0.get("mode") or (r0.get("params") or {}).get("mode") or "buy_hold",
        }
        if kind == "strategy":
            meta.pop("mode", None)
        if kind == "universe":
            meta.pop("universe", None)

    # Overlay series: each run + SPY from first run
    series = []
    for r in runs:
        eq = r.get("equity") or []
        series.append(
            {
                "label": _backtest_run_label(r, compare_kind=kind),
                "labels": [p["dt"] for p in eq],
                "values": [p["strategy_equity"] for p in eq],
            }
        )
    if runs:
        eq0 = runs[0].get("equity") or []
        series.append(
            {
                "label": "SPY B&H",
                "labels": [p["dt"] for p in eq0],
                "values": [p["spy_equity"] for p in eq0],
            }
        )

    return templates.TemplateResponse(
        request,
        "backtest_compare.html",
        {
            "request": request,
            "page_title": title,
            "title": title,
            "query_token": qtok,
            "rows": rows,
            "meta": meta,
            "payload": {"series": series, "kind": kind},
        },
    )


@app.get("/backtest/{run_id}", response_class=HTMLResponse)
def backtest_result(request: Request, run_id: str) -> Any:
    from trader.backtest.store import load_run

    qtok = request.query_params.get("token") or ""
    run = load_run(run_id)
    payload = None
    if run:
        payload = {
            "id": run["id"],
            "equity": run.get("equity") or [],
            "trades": run.get("trades") or [],
            "metrics": run.get("metrics") or {},
        }
    return templates.TemplateResponse(
        request,
        "backtest_result.html",
        {
            "request": request,
            "page_title": f"Backtest {run_id}",
            "query_token": qtok,
            "run": run,
            "payload": payload or {"equity": [], "trades": [], "metrics": {}},
        },
    )



def _trail_state_index() -> dict[str, dict[str, Any]]:
    """Return {symbol: {peak, floor, rungs, swept, initial_qty}} from position_trail_state.

    Failure-tolerant: if the table doesn't exist yet (fresh boot before the first
    position_health tick) we return an empty index and the UI just shows blanks
    in the new columns. Never raises.
    """
    try:
        with _conn() as c:
            rows = c.execute(
                """SELECT symbol, peak_unrealized_pnl,
                          profit_ladder_rungs_fired, expiry_sweep_fired, initial_qty
                   FROM position_trail_state"""
            ).fetchall()
    except sqlite3.OperationalError:
        return {}
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        out[(r["symbol"] or "").upper()] = {
            "peak": float(r["peak_unrealized_pnl"] or 0),
            "rungs_fired": str(r["profit_ladder_rungs_fired"] or ""),
            "expiry_swept": bool(r["expiry_sweep_fired"]),
            "initial_qty": int(r["initial_qty"] or 0),
        }
    return out


@app.get("/positions", response_class=HTMLResponse)
def positions_page(request: Request) -> Any:
    qtok = request.query_params.get("token") or ""
    rows: list[dict[str, Any]] = []
    err: str | None = None
    ac_strip: dict[str, Any] | None = None
    try:
        from datetime import date as _date

        from trader.occ import parse_occ_us_option_symbol

        try:
            giveback = float(os.getenv("TRAIL_GIVEBACK_PCT", "0.40") or 0.40)
        except ValueError:
            giveback = 0.40
        trail_idx = _trail_state_index()
        today = _date.today()

        tc = trading_client()
        for p in tc.get_all_positions():
            sym = (p.symbol or "").upper()
            ts = trail_idx.get(sym, {})
            peak = ts.get("peak")
            floor = (peak * (1.0 - giveback)) if (peak and peak > 0) else None

            occ = parse_occ_us_option_symbol(sym)
            dte = (occ["expiry"] - today).days if occ else None

            rungs_raw = (ts.get("rungs_fired") or "").strip()
            rungs_fired = [int(x) for x in rungs_raw.split(",") if x.strip().isdigit()]

            rows.append(
                {
                    "symbol": sym,
                    "qty": float(p.qty),
                    "avg_entry": float(p.avg_entry_price),
                    "market_value": float(p.market_value),
                    "unrealized_pl": float(p.unrealized_pl),
                    "unrealized_plpc": float(p.unrealized_plpc),
                    "peak_unrealized_pnl": peak,
                    "trail_floor": floor,
                    "days_to_expiry": dte,
                    "is_option": bool(occ),
                    "ladder_rungs_fired": rungs_fired,
                    "expiry_swept": bool(ts.get("expiry_swept")),
                }
            )
        acc = tc.get_account()
        ac_strip = {
            "equity": float(acc.equity),
            "daytrade_count": int(getattr(acc, "daytrade_count", 0) or 0),
            "pattern_day_trader": bool(getattr(acc, "pattern_day_trader", False)),
        }
    except Exception as e:
        err = str(e)

    return templates.TemplateResponse(
        request,
        "positions.html",
        {
            "query_token": qtok,
            "positions": rows,
            "positions_error": err,
            "account_strip": ac_strip,
        },
    )


@app.get("/cycle/{row_id}", response_class=HTMLResponse)
def cycle_detail(request: Request, row_id: int) -> Any:
    qtok = request.query_params.get("token") or ""
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM trades WHERE id = ?", (row_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, "Cycle not found")
    d = dict(row)
    logic = _parse_logic(d.get("logic_json"))
    logic_pretty = json.dumps(logic, indent=2, default=str) if logic else None

    snap_ex = raw_dec = final_dec = exe = None
    if isinstance(logic, dict):
        snap_ex = logic.get("snapshot_excerpt")
        raw_dec = logic.get("raw_decision")
        final_dec = logic.get("final_decision")
        exe = logic.get("execute")

    def _pretty(obj: Any) -> str | None:
        if obj is None:
            return None
        try:
            return json.dumps(obj, indent=2, default=str)
        except TypeError:
            return str(obj)

    model_confidence = None
    if isinstance(raw_dec, dict):
        model_confidence = raw_dec.get("confidence")
    post_reason = None
    if isinstance(final_dec, dict):
        post_reason = final_dec.get("reason")

    # Prefer the stored column; fall back to parsing logic_json for old rows
    exec_status = d.get("execute_status") or (
        exe.get("status") if isinstance(exe, dict) else None
    )
    exec_error = d.get("execute_error") or (
        exe.get("reason") if isinstance(exe, dict) and exe.get("status") == "error" else None
    )
    exec_order_id = d.get("order_id")

    pipeline = build_pipeline(logic, d) if isinstance(logic, dict) else None
    display_rationale = enrich_rationale(d.get("rationale"), logic)

    market_regime = None
    if isinstance(snap_ex, dict):
        mr = snap_ex.get("market_regime")
        if isinstance(mr, dict) and mr.get("direction") is not None:
            market_regime = mr

    return templates.TemplateResponse(
        request,
        "detail.html",
        {
            "row": d,
            "display_rationale": display_rationale,
            "query_token": qtok,
            "logic_pretty": logic_pretty,
            "snapshot_excerpt_pretty": _pretty(snap_ex),
            "raw_decision_pretty": _pretty(raw_dec),
            "final_decision_pretty": _pretty(final_dec),
            "execute_pretty": _pretty(exe),
            "model_confidence": model_confidence,
            "post_reason": post_reason,
            "exec_status": exec_status,
            "exec_error": exec_error,
            "exec_order_id": exec_order_id,
            "page_title": f"Cycle {row_id} — {d.get('symbol', '')}",
            "pipeline": pipeline,
            "market_regime": market_regime,
        },
    )


def _render_architecture_markdown(raw: str) -> str:
    import markdown

    return markdown.markdown(
        raw,
        extensions=["extra", "sane_lists", "nl2br", "fenced_code"],
        output_format="html",
    )


@app.get("/architecture/", include_in_schema=False)
def architecture_page_trailing_slash(request: Request) -> RedirectResponse:
    """Some clients/proxies request a trailing slash; normalize to /architecture."""
    q = request.url.query
    loc = "/architecture" + (f"?{q}" if q else "")
    return RedirectResponse(loc, status_code=307)


@app.get("/architecture", response_class=HTMLResponse)
def architecture_page(request: Request) -> Any:
    """Long-form architecture doc (mirrors docs/ARCHITECTURE.md in the repo)."""
    qtok = request.query_params.get("token") or ""
    md_path = _architecture_md_path()
    if not md_path.is_file():
        raise HTTPException(
            status_code=404,
            detail=(
                f"Architecture doc not found at {md_path}. "
                "Set ARCHITECTURE_DOC_PATH or ensure docs/ARCHITECTURE.md exists under the repo root."
            ),
        )
    try:
        md_text = md_path.read_text(encoding="utf-8")
    except OSError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    try:
        html_body = _render_architecture_markdown(md_text)
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"Markdown render failed: {e}. Install: pip install markdown",
        ) from e
    return templates.TemplateResponse(
        request,
        "architecture.html",
        {
            "query_token": qtok,
            "page_title": "Architecture — v2 red-day",
            "architecture_html": html_body,
        },
    )


@app.get("/blog")
def blog_redirect(request: Request) -> RedirectResponse:
    q = request.url.query
    loc = "/" + (f"?{q}" if q else "")
    return RedirectResponse(loc, status_code=307)


@app.get("/blog/{slug}")
def blog_slug_redirect(request: Request, slug: str) -> RedirectResponse:
    q = request.url.query
    loc = "/" + (f"?{q}" if q else "")
    return RedirectResponse(loc, status_code=307)


@app.get("/flow")
def flow_redirect(request: Request) -> RedirectResponse:
    q = request.url.query
    loc = "/architecture" + (f"?{q}" if q else "")
    return RedirectResponse(loc, status_code=307)
