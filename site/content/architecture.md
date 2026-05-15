# AI trading bot — public architecture reference

This document is a **sanitized, educational** overview of how a research-style automated equity/options stack can be structured: processes, data flow, persistence, and a read-only operator viewer. It is **not** live trading advice and may omit or generalize details compared to any private deployment.

---

## 1. Runtime topology

| Piece | Role |
| --- | --- |
| **Main service** | Runs `python -m trader.main` — blocking scheduler driving the trading loop. |
| **APScheduler** | **Base cron** during US equity regular hours on weekdays (± small jitter). Optional extra ET slots can add more runs. A **position-health** interval (configurable; set to zero to disable) can run automated trail exits and related checks **without** an LLM. An **end-of-day summary** runs shortly after the cash close to write a stats digest with **no** model call. The main cycle is a no-op outside **US equity RTH** (weekdays, exchange calendar). |
| **Log viewer service** | Serves a FastAPI app — **read-only** inspection of the journal database and live broker state, plus an optional human-readable **blog** route. |

**Secrets and configuration:** API keys, broker tokens, model keys, and log paths live in a **host-managed environment file** — never commit real values. Use your platform’s secret store in production.

**Default data paths** (names are configurable via environment variables):

| Artifact | Typical relative path |
| --- | --- |
| SQLite journal | `data/logs/trades.db` (tables such as `trades`, `risk_state`, `position_trail_state`) |
| JSONL mirror | `data/logs/journal.jsonl` |
| Optional metrics JSONL | Configurable path — one summary object per finished main cycle |
| Weekly reports | `data/reports/` (latest review, feedback file, dated weekly files) |
| EOD digest | `data/reports/` stable and dated end-of-day markdown |
| Legacy weekly text dump | `data/logs/` dated review text (if enabled) |

---

## 2. Per-cycle pipeline (one symbol)

Each scheduled run during regular hours first resolves a **cross-asset regime**: one small-model JSON verdict (direction / conviction / bearish ETF tickers), **TTL-cached** from benchmark equity snapshot slices plus macro, volatility, prediction markets, positioning, and sentiment-style inputs. Then the **watchlist** builder constructs the symbol universe — capped screener list, anchors first, optional **bearish hedge tickers** when conviction is high (skipped when a **static override list** is set), plus **all** open-position underlyings when that include-open-positions flag is on (held rows are not truncated). **For each symbol** the same pipeline runs in the main module:

```text
get_market_regime()               # once per cycle (cached)
  → get_watchlist(regime=...)
  → get_market_snapshot(symbol, regime=...)   # attaches regime verdict to dict
  → get_decision(snapshot)        # model JSON trade proposal
  → validate(...)                 # risk policy
  → execute(...)  (if BUY/SELL)  # broker orders
  → write_journal_entry(...)     # persistence
cycle_summary log + optional metrics JSONL
ping external healthcheck (ok | not ok)
```

After all symbols, the main loop emits a structured **cycle summary** log line (duration, symbol count, action counts, error flag, failed symbols, slow timings, and a compact regime object). If a metrics path is set, the same payload can be appended as one JSON line.

Errors inside the per-symbol `try` mark the cycle as failed for external monitoring; the loop continues for other symbols.

---

## 3. Market snapshot (`data_feed`)

`get_market_snapshot` builds one **dict** per symbol:

1. **Bars and indicators** — broker daily history plus technical analysis (RSI, MACD, EMAs, ATR, ADX, stochastic RSI, OBV, Bollinger, volume ratio, etc.).
2. **Live fields** — exchange-backed snapshot where available: price, VWAP, volume, previous close, change %.
3. **Account / position / portfolio** — From the trading API: equity, cash, PnL hints, day-trade count, open position for this symbol, plus **portfolio_context** (all open positions and, when sector lookup is enabled, **sector_notional_pct** by style label).
4. **Enrichment** — earnings hints, macro, options context, sentiment layers, etc. Each sub-module is **best-effort** and TTL-cached; missing keys are normal when optional API keys are unset.
5. **`data_quality`** — Completeness hints (`missing_critical`, `missing_optional`, `staleness_sec`) so the model and journal know when the snapshot is incomplete.
6. **`market_regime`** — When the main loop passes the cycle’s regime object into `get_market_snapshot`, each per-symbol dict includes direction, conviction, and rationale from the pre-brain (same verdict for every symbol that cycle).
7. **`symbol_sector`** — Best-effort equity sector via a cached lookup module. A disable flag skips network calls.

The object is what the **model** sees (as JSON). A **redacted excerpt** is stored for UI and audits (includes `data_quality`, `market_regime`, sector hints, and portfolio sector notionals).

---

## 4. Brain (`claude_brain`)

**Default:** a single **Trader** call returns one structured JSON decision (`action`, `strategy`, `confidence`, stops, rationale, …). The system prompt is **directional momentum** (bullish and bearish recipes; `market_regime` in the snapshot when enabled). The journal stores a compact **debate** block for continuity with older rows.

**Tier-2 policy in code:** earnings blackout, RSI extreme without trend (ADX), IV-rank half-size, pattern-day-trade ceiling under a configured equity floor, unknown sector cap, minimum option days to expiry, overnight-hold guard on discretionary sells, intraday giveback halt for new buys — all enforced in the risk module after the model output.

**Parse robustness:** The model occasionally returns prose without a JSON object. The brain does bounded retries (initial → reminder → final-strict mode) before converting failure into a deterministic **safe-HOLD** shell so one symbol cannot halt the cycle.

**Legacy:** a flag can restore an older single-call prompt path.

**Weekly feedback:** After a successful weekly review, a short bullet file holds critique text. If the file is fresh, its text is **prepended** to the user payload as historical critique — **not** live market data.

**`data_quality`:** Shared rules require **HOLD** when `missing_critical` is non-empty so the model does not trade on broken snapshots.

---

## 5. Risk engine (`risk_engine`)

`validate` runs **after** the model output:

- **Tier 1** — Structural safety: bad equity, daily loss / drawdown **HALT**, malformed option fields, allowlists.
- **Tier 2** — Confidence floor, optional volatility-regime scaling, symbol cooldown after sell, concurrent buy cap, **pattern-day-trade ceiling** when equity is below a floor, size clamp, optional max sector notional vs sector labels, earnings blackout, RSI+ADX chop filter, IV-rank half-size on option buys, **minimum option DTE**, **overnight hold** on discretionary sells (automation bypass via exit kind), **intraday giveback halt** for new buys (session flag in risk state).
- **Add-to-position** — When disabled, a second buy on an already-held long stock symbol is blocked. A flag allows adding to an open equity position.
- **Minimum option hold** — Evaluated before overnight hold: blocks a sell on long options unless the position has been open at least a configured duration, with profit-based bypass rules.

A **soft policy** relaxes some Tier-2 behavior to warnings for paper experiments — buys may still execute even at the PDT ceiling. Do not enable for accounts approaching pattern-day-trader limits.

Output is a **final** decision dict; overrides are reflected in stored logic metadata.

---

## 6. Executor (`executor`)

- **long_stock** — BUY submits **GTC bracket** orders; stop/take-profit distances respect ATR multipliers from configuration. Price clamps defend against extreme sub-dollar names where a naive stop would be non-positive.
- **SELL** — DAY market order to flatten. Bracket-only fields are **never** attached to a sell.
- **long_call / long_put** — Single-leg options only; quantity follows configured tiers and floors/ceilings. Quote mid × contract multiplier feeds the budget step.

### Option liquidity gate (default on)

Dormant option contracts (real ask, no bid) can trap positions: the buy succeeds at the ask, but the next sell may fail with “no available quote”. The executor can require positive bid and ask before buys and a positive bid before market sells. Unsupported structures are rejected earlier.

---

## 7. Journal (`journal`)

`write_journal_entry` persists:

- SQLite row in **trades** (symbol, action, strategy, confidence, rationale, equity snapshot, timestamps, execute columns, full **logic_json**).
- One **JSONL** line mirroring high-signal metadata for grep/tail pipelines.

`logic_json` includes: snapshot excerpt (with `data_quality`, sector hints), raw model output, final post-risk decision, execute block (option buys often include sizing metadata), optional **debate** transcript.

**Concurrent access:** SQLite connections go through a shared helper that enables **WAL** mode and a **busy timeout** so a read-only viewer can keep reading while the bot commits.

**Size-based rotation:** The JSONL file can be renamed with a timestamp suffix once it exceeds a max byte size, then oldest rotated siblings beyond a retention count are pruned. The weekly reporter walks rotated siblings alongside the live file so review windows survive rotation.

**Human change log:** An optional path can be read by the weekly reporter; recent entries are prepended to the weekly payload so reviewers see behavioral changes alongside trades.

---

## 8. Read-only web viewer (`trader/web`)

FastAPI app. Routes can share an optional **query token** guard when configured.

| Route | Purpose |
| --- | --- |
| `GET /` | Cycle table from SQLite. |
| `GET /cycle/{id}` | One cycle: pipeline strip + expandable `logic_json`. |
| `GET /positions` | Live positions + account strip. |
| `GET /flow` | Mermaid architecture diagram + stage prose. |
| `GET /architecture` | Rendered operator architecture doc (private copy). |
| `GET /report/latest.md` | Raw Markdown weekly blog (latest). |
| `GET /healthz` | JSON smoke check for database connectivity. |

---

## 9. Intraday background jobs

### Position health (interval, optional)

Runs during US RTH on every tick of the configured interval (set to `0` to disable). Purposes:

1. **Session risk** — tracks intraday daily P&L peak for **giveback halt** (new buys), and optional **daily-loss emergency flatten** plus halt-until timestamp.
2. **Position log** — fetches all open positions and emits a structured log line.
3. **Trail giveback exits** — maintains per-symbol peak unrealized P&L; fires automated exits when giveback from peak exceeds a threshold (multi-day unless reset-daily is enabled). Each automated exit journals with a clear automation rationale.
4. **Options hard stop** — full close when unrealized loss exceeds a configured floor.
5. **Profit ladder / expiry sweep** — as configured in project docs.

### End-of-day summary (weekdays after the close, with jitter)

No model call. Queries today’s SQLite rows and the live account, then writes dated and stable markdown files under the reports directory.

---

## 10. Weekly offline pipeline

**Not** on the intraday scan scheduler. Typically **cron** (e.g. Sunday) runs:

```bash
python -m trader.weekly_review
```

That module aggregates JSONL + SQLite (and optional human journal path / closed orders), attaches an **operating snapshot** (resolved watchlist size, option tier caps, trail giveback %) for prompt context, calls a larger model once, and writes dated review markdown, JSON, feedback bullets, and blog artifacts surfaced in the operator viewer.

---

## 11. Diagram vs code

The **flow** Mermaid diagram groups feeds, the per-cycle pipeline, sinks, and a **weekly** subgraph. Arrow semantics:

- **Solid** — Primary data/control flow during a cycle or weekly job.
- **Dashed** — Reads of historical data (e.g. JSONL/SQLite into weekly review; feedback into brain).

The diagram is **schematic**; exact function names and configuration gates are in source and operator documentation.

---

## 12. Where to read next

| Document | Use when |
| --- | --- |
| Operator trading guide | Install, environment variables, process supervision, UI usage. |
| Secrets hygiene doc | Key rotation and safe defaults. |
| Homelab narrative (if published) | Broader context and philosophy. |

---

## 13. Module index (quick)

| Module | Responsibility |
| --- | --- |
| `trader/main.py` | Scheduler, main cycle, position health, end-of-day summary, external health ping. |
| `trader/market_hours.py` | Regular-hours gate + holiday set (shared with web). |
| `trader/regime.py` | Cross-asset verdict, TTL cache, bearish vehicle hints; threads into watchlist + snapshots. |
| `trader/watchlist.py` | Symbol universe: screener cap + anchors + optional bearish tickers + uncapped held underlyings; optional quality filters. |
| `trader/data_feed.py` | Snapshot assembly + data quality + sector breakdown. |
| `trader/data_quality.py` | Snapshot completeness hints. |
| `trader/sector.py` | Cached sector labels. |
| `trader/indicators.py` | Technical analysis from bars. |
| `trader/claude_brain.py` | Model decisions + weekly feedback prefix. |
| `trader/risk_engine.py` | Policy + HALT. |
| `trader/executor.py` | Broker orders. |
| `trader/journal.py` | SQLite + JSONL. |
| `trader/db.py` | SQLite helper — WAL + busy timeout for writer/reader concurrency. |
| `trader/alpaca_runtime.py` | Shared broker clients. |
| `trader/weekly_review.py` | Weekly entrypoint. |
| `trader/reporting/weekly_report.py` | Weekly aggregates + artifact writers. |
| `trader/web/app.py` | FastAPI routes and templates. |
| `tests/` | Regression tests. |

---

*This page is a curated public summary. It intentionally omits private host paths, VPN details, and non-public URLs.*
