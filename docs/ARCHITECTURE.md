# AI trading bot — architecture reference

This document describes how the **runnable** stack under `/srv/ai-trading-bot` is structured: processes, data flow, persistence, and the read-only web UI. It complements the historical homelab narrative in `docs/legacy/Homelab_spec.md` and the operations guide in `docs/TRADING_BOT.md`.

---

## 1. Runtime topology


| Piece                             | Role                                                                                                                                                    |
| --------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **systemd `trading-bot`**         | Runs `python -m trader.main` — blocking scheduler.                                                                                                      |
| **APScheduler**                   | **Base cron** at **10:00, 12:00, 14:00, 15:30 ET** Mon–Fri (±60s jitter). Optional **`SCHEDULE_EXTRA_ENABLE`** adds more ET slots from **`SCHEDULE_EXTRA_SLOTS`** (default `11:30,13:30`). **`POSITION_HEALTH_INTERVAL_MIN`** (default **5 min**, set to `0` to disable) runs **`run_position_health`** on an interval — logs open positions and fires rule-based **trail giveback exits** (no LLM; see below). **`run_eod_summary`** runs at **16:05 ET** Mon–Fri to write a stats digest with no LLM call (see below). `run_cycle` is a no-op outside **US equity RTH** (Mon–Fri 09:30–16:00 ET, minus NYSE holidays). |
| **systemd `trading-logview`**     | Serves the FastAPI app (`uvicorn trader.web.app:app`) — **read-only** inspection of SQLite + live Alpaca, plus the human-readable weekly **blog** at `/blog`. |


**Secrets and env:** `/root/.openclaw/api.env` (legacy path, retained for compatibility; symlinked from repo `.env`). Alpaca, Anthropic, optional enrichers, log paths, and viewer token live there.

**Default data paths** (override with `TRADING_LOG_DIR` / `TRADING_DB_PATH` / `TRADING_JSONL_PATH` / `DATA_REPORTS_DIR`):


| Artifact                | Typical path                                                                    |
| ----------------------- | ------------------------------------------------------------------------------- |
| SQLite journal          | `data/logs/trades.db` (tables: `trades`, `risk_state`, `position_trail_state`) |
| JSONL mirror            | `data/logs/journal.jsonl`                                                       |
| Optional metrics JSONL  | Set **`TRADING_METRICS_JSONL_PATH`** — one summary object per finished `run_cycle` |
| Weekly reports          | `data/reports/` (`LATEST_REVIEW.md`, `TRADER_FEEDBACK.md`, dated `weekly-*.md`) |
| EOD digest              | `data/reports/LATEST_EOD.md` + dated `eod-YYYY-MM-DD.md` (written by `run_eod_summary` at 16:05 ET) |


---

## 2. Per-cycle pipeline (one symbol)

Each scheduled run during RTH first resolves a **cross-asset regime** (`trader/regime.py` → **`get_market_regime()`**): one **Haiku** JSON verdict (direction / conviction / bearish ETF tickers), **TTL-cached** (`REGIME_TTL_SEC`, default 15 min) from SPY/QQQ snapshot slices plus macro/VIX/Fear & Greed leading signals. Then **`get_watchlist(regime=...)`** (`trader/watchlist.py`) builds the symbol universe — Alpaca most-actives capped at **`WATCHLIST_SCREENER_SIZE`** (default **20**; legacy **`WATCHLIST_SIZE`** when unset), anchors first, optional **bearish hedge tickers** when regime is bearish above **`REGIME_BEARISH_INJECT_THRESHOLD`** (skipped when **`TRADING_WATCHLIST`** static override is set), plus **all** open-position underlyings when **`WATCHLIST_INCLUDE_OPEN_POSITIONS`** is on (held rows are never truncated). **For each symbol** the same pipeline runs in `trader/main.py` → `run_cycle()`:

```text
get_market_regime()               # trader/regime.py — once per cycle (cached)
  → get_watchlist(regime=...)     # trader/watchlist.py
  → get_market_snapshot(symbol, regime=...)   # trader/data_feed.py — adds market_regime to dict
  → get_decision(snapshot)        # trader/claude_brain.py
  → validate(...)                 # trader/risk_engine.py
  → execute(...)  (if BUY/SELL)  # trader/executor.py
  → write_journal_entry(...)     # trader/journal.py
cycle_summary log + optional metrics JSONL  # trader/main.py (includes market_regime summary)
ping_healthcheck(ok | not ok)
```

After all symbols, **`main.run_cycle`** emits a structured **`cycle_summary`** log line (duration, symbol count, action counts, error flag, **`failed_symbols`**, **`slowest_symbols_ms`**, and a compact **`market_regime`** object — top-5 per-symbol elapsed time so a single hung enrichment source is visible after the fact). If **`TRADING_METRICS_JSONL_PATH`** is set, the same payload is appended as one JSON line.

Errors inside the per-symbol `try` mark the cycle as failed for Healthchecks; the loop continues for other symbols.

<!-- SYNC:profiles -->
### Trading profiles

A **profile** selects preset behavior without rewriting the pipeline: **`default`** (screener watchlist, stock + options, base-hit exits) vs an aggressive **options-only** paper profile (fixed high-beta universe, short DTE, moonshot profit ladder, relaxed paper risk limits).

The risk layer still enforces structural kill-switches (drawdown, PDT, liquidity). Journal rows carry a **profile tag** for weekly attribution. Live deployment of the aggressive profile requires an explicit operator acknowledgment after paper metrics pass.
<!-- /SYNC:profiles -->

<!-- SYNC:research -->
### Research and evaluation (offline)

Separate from live trading: replay journal and account snapshots into **equity / drawdown charts**, **exit-reason attribution**, and a **paper promotion checklist** (win rate, profit factor, trade count, max drawdown).

A private operator dashboard renders these charts; the public site documents methodology only. Fixed-rule historical backtests are a planned Phase 2 layer on top of this replay pipeline.
<!-- /SYNC:research -->

### Operator detail: `yolo_options` (homelab)

When `TRADING_PROFILE=yolo_options` (see `docs/PROFILES.md`, `.env.yolo.example`):

| Area | Behavior |
| --- | --- |
| Watchlist | `TRADING_WATCHLIST` CSV; `WATCHLIST_SCREENER_SIZE=0` |
| Strategies | `long_call`, `long_put` only |
| Sizing | Up to ~12% equity; tiers up to 5 contracts |
| Exits | Moonshot ladder 2/5/10×; trail 55% giveback; health every 2 min |
| Tier-2 | 5% daily HALT; no flatten-all by default; `MIN_CONFIDENCE=0.60` |
| Brain | `YOLO_TRADER_SYSTEM_PROMPT` in `claude_brain.py` |
| Research | `GET /research` — narrative + charts + promotion gate |

---

## 3. Market snapshot (`data_feed`)

`get_market_snapshot` builds one **dict** per symbol:

1. **Bars and indicators** — Alpaca daily history + `trader/indicators.py` (RSI, MACD, EMAs, ATR, ADX, Stoch RSI, OBV, Bollinger, volume ratio, etc.).
2. **Live fields** — IEX-backed snapshot where available: price, VWAP, volume, prev close, change %.
3. **Account / position / portfolio** — From Alpaca trading API: equity, cash, PnL hints, `daytrade_count`, open position for this symbol, plus **`portfolio_context`** (all open positions and, when sector lookup is enabled, **`sector_notional_pct`** by GICS-style label).
4. **Enrichment** — `trader/enrichment.py`, `macro.py`, options ATM context, sentiment layers, etc. Each sub-module is **best-effort** and TTL-cached; missing keys are normal when API keys are unset.
5. **`data_quality`** — `trader/data_quality.py` adds `missing_critical`, `missing_optional`, and `staleness_sec` so the brain and journal know when the snapshot is incomplete.
6. **`market_regime`** — When `run_cycle` passes the cycle’s `MarketRegime` into `get_market_snapshot`, each per-symbol dict includes `direction`, `conviction`, and `rationale` from the pre-brain (same verdict for every symbol that cycle).
7. **`symbol_sector`** — Best-effort equity sector via **`trader/sector.py`** (yfinance, TTL-cached). Set **`SECTOR_LOOKUP_DISABLE=true`** to skip network calls.

The object is what **Claude** sees (as JSON). A **redacted excerpt** is stored in `logic_json.snapshot_excerpt` for the UI and audits (includes `data_quality`, `market_regime`, `symbol_sector`, and **`portfolio_sector_notional_pct`**).

---

## 4. Brain (`claude_brain`)

**Default:** a single **Trader** call (**Haiku** by default via `BRAIN_TRADER_MODEL`) returns one structured JSON decision (`action`, `strategy`, `confidence`, stops, rationale, …). The system prompt is **directional momentum** (bullish and bearish recipes; `market_regime` in the snapshot when enabled). The journal stores a compact **`debate`** block (`mode`, `trader`, optional pre-flight skip metadata) for continuity with older rows.

**Tier-2 policy in code (2026-05-15):** earnings blackout, RSI extreme without trend (ADX), IV-rank half-size, Alpaca PDT ceiling under $25k, Unknown sector cap, minimum option DTE, overnight-hold guard on discretionary SELLs, intraday giveback halt for new BUYs — all enforced in `trader/risk_engine.py` after the model output (see §5).

**Parse robustness:** Claude occasionally returns prose without a JSON object. `_call_and_parse` does up to **two retries** (initial → reminder → final-strict mode with bumped `max_tokens`) before raising a typed `_ModelParseError`. `get_decision` converts that into a deterministic **safe-HOLD** shell (`action=HOLD`, `confidence=0.0`, `_parse_fallback=true`) so one symbol cannot halt the cycle. Logs `WARNING trader_parse_fallback` with a 240-char snippet.

**Weekly feedback:** After each successful weekly review, `data/reports/TRADER_FEEDBACK.md` holds short bullets. If the file is fresh (mtime within `TRADER_FEEDBACK_MAX_AGE_DAYS`, default 8), its text is **prepended** to the user payload as historical critique — **not** live market data (`trader/claude_brain.py`).

**`data_quality`:** Shared rules require **HOLD** when `data_quality.missing_critical` is non-empty so the model does not trade on broken snapshots.

---

## 5. Risk engine (`risk_engine`)

`validate` runs **after** the model output:

- **Tier 1** — Structural safety: bad equity, daily loss / drawdown **HALT**, malformed option fields, allowlists.
- **Tier 2** — Confidence floor, optional VIX-regime scaling, symbol cooldown after SELL, concurrent BUY cap, **Alpaca `daytrade_count` PDT ceiling** when `equity < PDT_EQUITY_FLOOR`, `size_pct` clamp, optional **`MAX_SECTOR_NOTIONAL_PCT`** / **`MAX_UNKNOWN_SECTOR_PCT`** vs **`snapshot.symbol_sector`** and **`portfolio_context.sector_notional_pct`**, earnings blackout, RSI+ADX chop filter, IV-rank half-size on option BUYs, **minimum option DTE**, **overnight hold** on discretionary SELLs (automation bypass via `risk_exit_kind`), **intraday giveback halt** for new BUYs (session flag in `risk_state`).
- **`MAX_ADD_TO_POSITION`** (default off) — when false, a second BUY on an already-held `long_stock` symbol is blocked with HOLD. Set `MAX_ADD_TO_POSITION=true` to allow adding to an open equity position.
- **`OPTIONS_MIN_HOLD_HOURS`** (default **0.5 h**) — evaluated **before** overnight hold: blocks a SELL on `long_call` / `long_put` unless the position has been open at least this long. Bypass: if `unrealized_pct ≥ PROFIT_LOCK_PCT` (default **20 %**), min-hold is waived and a warning is logged; same unrealized threshold also bypasses **overnight** hold for options take-profit symmetry.

`RISK_SOFT_POLICY` relaxes some Tier-2 behavior to warnings for paper experiments. `RISK_SOFT_POLICY=true` downgrades PDT ceiling, symbol cooldown, and concurrent-cap blocks to `risk_warnings` only — **BUYs may still execute** even at the PDT ceiling. Do not enable for accounts approaching pattern-day-trader limits.

Output is a **final** decision dict; overrides are reflected in `logic_json` (`post_risk_*` style fields where applicable).

---

## 6. Executor (`executor`)

- **long_stock** — BUY submits **GTC bracket** orders; stop/TP distances respect ATR multipliers from env. `_clamp_bracket_prices` returns `None` (caller skips with `atr_clamp_infeasible_stop_tp`) when the ATR floor would produce a non-positive stop — defense-in-depth against sub-dollar names like EZGO where `ref - ATR` goes negative.
- **SELL** — DAY market order to flatten. Bracket-only fields (`stop_loss`, `take_profit`) are **never** attached to a SELL — fixes the historical "missing stop_loss" rejection class on closing trades for SPY/MSFT.
- **long_call / long_put** — Single-leg options only; quantity is `min(budget from size_pct, conviction_contract_cap(confidence))` from `OPTIONS_SIZE_TIERS` + `MAX_OPTIONS_CONTRACTS_FLOOR` / `MAX_OPTIONS_CONTRACTS_HARD` (see `docs/MAGIC_NUMBERS.md`). Quote mid × 100 feeds the budget step.

### Option liquidity gate (`OPTIONS_REQUIRE_BID`, default **true**)

Dormant option contracts (real ask, no bid) used to trap positions: the BUY succeeded at the ask, but the next SELL got Alpaca `40310000` ("no available quote for symbol") and the position spammed the journal with retry-error pairs each cycle. The executor now pre-checks the bid via a shared `options_exec.fetch_option_bid_ask` helper:

- **BUY:** requires `bid > 0` **and** `ask > 0` before submitting. Rejection reason: `option_no_bid_at_buy`.
- **SELL:** requires `bid > 0` before submitting a market exit. Rejection reason: `option_no_bid_for_market_sell`. Position remains open; the next cycle will retry naturally if a real bid reappears.

Set `OPTIONS_REQUIRE_BID=false` to disable both checks. Unsupported structures (e.g. unimplemented spreads in live code paths) are rejected earlier.

---

## 7. Journal (`journal`)

`write_journal_entry` persists:

- SQLite row in `**trades`** (symbol, action, strategy, confidence, rationale, equity snapshot, timestamps, execute columns, full `**logic_json**`).
- One **JSONL** line mirroring high-signal metadata for grep/tail pipelines.

`logic_json` includes: snapshot excerpt (with `data_quality`, sector hints), raw model output, final post-risk decision, execute block (option BUY fills often include `option_qty` and `conviction_contract_cap` from sizing), optional **debate** transcript.

**Concurrent access:** All SQLite connections (writer in `trading-bot`, reader in `trading-logview`) go through **`trader/db.connect()`**, which enables **WAL** mode and a 15 s **`busy_timeout`** (override with `TRADING_DB_WAL` / `TRADING_DB_BUSY_TIMEOUT_MS`). WAL lets the viewer keep reading while the bot commits — no `database is locked` errors during a cycle.

**Size-based rotation:** Before each append, `_maybe_rotate_journal` renames `journal.jsonl` to `journal.jsonl.YYYYMMDD-HHMMSS` once it exceeds **`JOURNAL_MAX_BYTES`** (default **100 MB**), then prunes oldest rotated siblings beyond **`JOURNAL_KEEP_ROTATED`** (default **10**). Set `JOURNAL_MAX_BYTES=0` to disable. The weekly reporter's `load_jsonl_since` walks rotated siblings (mtime-ordered, oldest first) alongside the live file, so the 7-day review window survives rotation timing.

**Human change log:** `WEEKLY_HUMAN_JOURNAL_PATH` (defaults to `BUILD_LOG.md` in the repo root via `.env.example`) is read by the weekly reporter; recent entries are prepended to the **weekly review** payload so the reviewer sees the latest behavioral changes alongside the trades they affected.

---

## 8. Read-only web viewer (`trader/web`)

FastAPI app in `trader/web/app.py`. All browser routes share the same optional `**?token=`** guard when `TRADE_LOG_VIEW_TOKEN` is set.


| Route                   | Purpose                                                                              |
| ----------------------- | ------------------------------------------------------------------------------------ |
| `GET /`                 | Cycle table from SQLite.                                                             |
| `GET /cycle/{id}`       | One cycle: pipeline strip + expandable `logic_json`.                                 |
| `GET /positions`        | Live Alpaca positions + account strip.                                               |
| `GET /flow`             | Mermaid architecture diagram + stage-by-stage prose (same mental model as this doc). |
| `GET /architecture`     | Rendered **this** document from `docs/ARCHITECTURE.md`.                              |
| `GET /research`         | Offline eval charts + paper promotion gate (`trader/research/eval_pack.py`).       |
| `GET /report/latest.md` | Raw Markdown weekly blog (`LATEST_REVIEW.md`).                                       |
| `GET /healthz`          | JSON `{"ok", "rows"}` for DB smoke checks.                                           |


---

## 9. Intraday background jobs

### `run_position_health` (interval, default every 5 min)

Runs during US RTH on every tick of the `POSITION_HEALTH_INTERVAL_MIN` interval (set to `0` to disable). Purposes:

1. **Session risk** — tracks intraday daily P&L peak for **giveback halt** (new BUYs), and optional **daily-loss emergency flatten** + `halt_until_utc`. Note: `validate()` uses strict `<` while `run_position_health` emergency flatten uses `<=`; keep `DAILY_LOSS_EXIT_PCT` and `MAX_DAILY_LOSS_PCT` aligned unless asymmetric behavior is intentional.
2. **Position log** — fetches all open Alpaca positions and emits a `position_health` log line.
3. **Trail giveback exits** — for each open position it maintains a per-symbol peak unrealized P&L in the `position_trail_state` SQLite table. If the current unrealized P&L has given back more than `TRAIL_GIVEBACK_PCT` (default **40 %**) of the tracked **peak** unrealized P&L (multi-day unless `TRAIL_RESET_DAILY=true`), it fires a SELL (equity) or option SELL (via `parse_occ_us_option_symbol`), marks `exit_fired=1`, and calls `record_day_trade()`. The exit uses `executor.execute` directly — no LLM. Each automated exit calls `_journal_programmed_exit` which writes a full `trades` + JSONL row with `rationale=*_automation` and `logic_json.automated_exit.kind` set to the exit type.
4. **Options hard stop** — full close when `unrealized_plpc ≤ OPTIONS_HARD_STOP_PCT` (default **−50%**).
5. **Profit ladder / expiry sweep** — as configured (see `docs/MAGIC_NUMBERS.md`).

### `run_eod_summary` (Mon–Fri 16:05 ET, ±30 s jitter)

No LLM call. Queries today's SQLite rows (action counts, execute-status counts, equity range) and the live Alpaca account, then writes two files to `DATA_REPORTS_DIR`:

- `eod-YYYY-MM-DD.md` — dated copy.
- `LATEST_EOD.md` — stable path for the dashboard or any ad-hoc reader.

---

## 10. Weekly offline pipeline

**Not** on the intraday scan scheduler. Typically **cron** (e.g. Sunday) runs:

```bash
python -m trader.weekly_review
```

That module (`trader/weekly_review.py`) uses `trader/reporting/weekly_report.py` to aggregate JSONL + SQLite (and optional human journal path / Alpaca closed orders), attach a **`bot_operating_snapshot`** (resolved watchlist screener size, option tier caps, trail giveback %) for prompt context, calls **Sonnet** once, and writes:

- `weekly-YYYY-MM-DD.md` / `.json`
- `LATEST_REVIEW.md` — stable path for HTTP fetch (`GET /report/latest.md`)
- `TRADER_FEEDBACK.md` — distilled bullets for the next week’s brain context
- `blog-YYYY-MM-DD.md` + `blog-YYYY-MM-DD.charts.json` — human-readable post + SVG charts surfaced at `/blog` and `/blog/{slug}` in the dashboard.

---

## 11. Diagram vs code

The `**/flow`** Mermaid diagram groups feeds, the per-cycle pipeline, sinks, and a **weekly** subgraph. Arrow semantics:

- **Solid** — Primary data/control flow during a cycle or weekly job.
- **Dashed** — Reads of historical data (e.g. JSONL/SQLite into weekly review; feedback into brain).

The diagram is **schematic**; exact function names and env gates are in source and in `docs/TRADING_BOT.md`.

---

## 12. Where to read next


| Document                              | Use when                                    |
| ------------------------------------- | ------------------------------------------- |
| `docs/TRADING_BOT.md`                 | Install, env vars, systemd, cron, UI usage. |
| `docs/SECRETS.md`                     | Key hygiene.                                |
| `docs/legacy/Homelab_spec.md`         | Historical homelab spec (CT 410, Telegram, `/opt/trader` paths — not the live deployment). |


---

## 13. Module index (quick)


| Module                              | Responsibility                                       |
| ----------------------------------- | ---------------------------------------------------- |
| `trader/main.py`                    | Scheduler, `run_cycle`, `run_position_health` (trail exits), `run_eod_summary`, Healthchecks ping. |
| `trader/market_hours.py`            | `is_us_equity_rth` + NYSE holiday set (shared with `trader/web/app.py`). |
| `trader/regime.py`                  | `get_market_regime()` — cross-asset Haiku verdict, TTL cache, bearish vehicle allowlist; threads into watchlist + snapshots. |
| `trader/watchlist.py`               | Symbol universe per scheduled scan: screener cap + anchors + optional bearish hedge tickers + uncapped held underlyings; optional quality filters. |
| `trader/data_feed.py`               | Snapshot assembly + `data_quality` + sector breakdown. |
| `trader/data_quality.py`            | Snapshot completeness hints.                         |
| `trader/sector.py`                  | Cached yfinance sector labels.                       |
| `trader/indicators.py`              | TA from bars.                                        |
| `trader/claude_brain.py`            | LLM decisions + weekly feedback prefix.              |
| `trader/risk_engine.py`             | Policy + HALT.                                       |
| `trader/executor.py`                | Alpaca orders.                                       |
| `trader/journal.py`                 | SQLite + JSONL.                                      |
| `trader/db.py`                      | SQLite connection helper — WAL + `busy_timeout` for safe writer/reader concurrency. |
| `trader/alpaca_runtime.py`          | Shared Alpaca clients.                               |
| `trader/weekly_review.py`           | Weekly Sonnet entrypoint.                            |
| `trader/reporting/weekly_report.py` | Weekly aggregates + artifact writers; `bot_operating_snapshot` in review context.                |
| `trader/web/app.py`                 | FastAPI routes and templates.                        |
| `scripts/journal_forward_returns.py` | Offline forward-return report from SQLite (long_stock BUY; yfinance). |
| `tests/`                            | `pytest` regression tests (`pytest.ini` sets `pythonpath = .`). |


