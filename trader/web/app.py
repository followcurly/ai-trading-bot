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
from starlette.templating import Jinja2Templates

from trader.alpaca_runtime import trading_client
from trader.config import TRADE_LOG_VIEW_TOKEN, TRADING_PROFILE
from trader.research.eval_pack import PROMOTION_THRESHOLDS, build_eval_pack
from trader.research.narrative import (
    ACTION_GLOSSARY,
    EXIT_GLOSSARY,
    build_research_narrative,
)
from trader.reporting.research_charts import build_research_charts
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
            "action_glossary": ACTION_GLOSSARY,
            "exit_glossary": EXIT_GLOSSARY,
        },
        "action_glossary": ACTION_GLOSSARY,
        "exit_glossary": EXIT_GLOSSARY,
        "by_action": {},
        "by_execute_status": {},
        "exit_reason_counts": {},
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


@app.get("/report/latest.md")
def latest_weekly_report() -> PlainTextResponse:
    """Latest Sonnet weekly blog Markdown (same token guard as other routes)."""
    try:
        from trader.reporting.weekly_report import reports_dir

        p = reports_dir() / "LATEST_REVIEW.md"
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    if not p.is_file():
        raise HTTPException(
            status_code=404,
            detail="No weekly report yet. Run: python -m trader.weekly_review",
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


@app.get("/research", response_class=HTMLResponse)
def research_page(request: Request) -> Any:
    qtok = request.query_params.get("token") or ""
    prof = (request.query_params.get("profile") or TRADING_PROFILE).strip().lower()
    try:
        days = max(1, min(365, int(request.query_params.get("days") or "30")))
    except ValueError:
        days = 30
    pack = build_eval_pack(days=days, profile=prof)
    charts = build_research_charts(pack)
    narrative = build_research_narrative(pack)
    return templates.TemplateResponse(
        request,
        "research.html",
        {
            "query_token": qtok,
            "page_title": f"Research — {prof}",
            "profile": prof,
            "trading_profile": TRADING_PROFILE,
            "days": days,
            "charts": charts,
            "narrative": narrative,
            "action_glossary": narrative.get("action_glossary") or ACTION_GLOSSARY,
            "exit_glossary": narrative.get("exit_glossary") or EXIT_GLOSSARY,
            "by_action": pack.get("by_action") or {},
            "by_execute_status": pack.get("by_execute_status") or {},
            "exit_reason_counts": pack.get("exit_reason_counts") or {},
            "promotion": pack.get("promotion_metrics") or {},
            "thresholds": pack.get("thresholds") or PROMOTION_THRESHOLDS,
            "closed_trades": pack.get("closed_trades") or [],
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

        from trader.options_exec import parse_occ_us_option_symbol

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
            "page_title": "Architecture — how the bot works",
            "architecture_html": html_body,
        },
    )


@app.get("/blog", response_class=HTMLResponse)
def blog_index(request: Request) -> Any:
    """Human-facing weekly blog — the bot's journal in plain English."""
    qtok = request.query_params.get("token") or ""
    from trader.reporting.blog_generator import list_blog_posts
    posts = list_blog_posts()
    return templates.TemplateResponse(
        request,
        "blog.html",
        {
            "query_token": qtok,
            "page_title": "The Bot's Journal — weekly blog",
            "posts": posts,
        },
    )


@app.get("/blog/{slug}", response_class=HTMLResponse)
def blog_post_page(request: Request, slug: str) -> Any:
    """Single blog post."""
    qtok = request.query_params.get("token") or ""
    from trader.reporting.blog_generator import get_blog_post, list_blog_posts, reports_dir

    post = get_blog_post(slug)
    if not post:
        raise HTTPException(404, f"Blog post '{slug}' not found")

    # Load SVG charts sidecar if it exists
    charts: dict[str, str] = {}
    charts_path = reports_dir() / f"blog-{slug}.charts.json"
    if charts_path.is_file():
        try:
            charts = json.loads(charts_path.read_text(encoding="utf-8"))
        except Exception:
            charts = {}

    # Build prev/next slugs for navigation
    all_posts = list_blog_posts()
    slugs = [p["slug"] for p in all_posts]
    idx = slugs.index(slug) if slug in slugs else -1
    prev_slug = slugs[idx + 1] if idx >= 0 and idx + 1 < len(slugs) else None
    next_slug = slugs[idx - 1] if idx > 0 else None

    return templates.TemplateResponse(
        request,
        "blog_post.html",
        {
            "query_token": qtok,
            "page_title": post["title"],
            "post": post,
            "charts": charts,
            "prev_slug": prev_slug,
            "next_slug": next_slug,
        },
    )


@app.get("/flow", response_class=HTMLResponse)
def flow_page(request: Request) -> Any:
    """Architecture view: how the bot works, end-to-end."""
    qtok = request.query_params.get("token") or ""

    total_cycles = 0
    last_cycle_time: str | None = None
    try:
        with _conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS c, MAX(timestamp) AS t FROM trades"
            ).fetchone()
            if row is not None:
                total_cycles = int(row["c"] or 0)
                last_cycle_time = row["t"] or None
    except sqlite3.OperationalError:
        pass

    def _cadence_line() -> str:
        base = (
            "Base: 4× per market day at 10:00, 12:00, 14:00, and 15:30 ET (Mon–Fri), "
            "±60 s jitter; scans only during US RTH (09:30–16:00 ET, ex-NYSE holidays)."
        )
        parts: list[str] = [base]
        if os.getenv("SCHEDULE_EXTRA_ENABLE", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        ):
            slots = (os.getenv("SCHEDULE_EXTRA_SLOTS") or "11:30,13:30").strip()
            parts.append(f" Extra cron slots enabled: {slots} ET.")
        try:
            hm = int(os.getenv("POSITION_HEALTH_INTERVAL_MIN", "0") or "0")
        except ValueError:
            hm = 0
        if hm > 0:
            parts.append(f" Position health log every {hm} min (no LLM).")
        parts.append(
            " (Env is read from this process — trading-logview may differ from trading-bot.)"
        )
        return "".join(parts)

    return templates.TemplateResponse(
        request,
        "flow.html",
        {
            "query_token": qtok,
            "total_cycles": total_cycles,
            "last_cycle_time": last_cycle_time,
            "scheduler_cadence_line": _cadence_line(),
            "trading_profile": TRADING_PROFILE,
            "page_title": "How the bot works — data flow",
        },
    )
