# Architecture (v2)

How I wired the simplified paper stack. Educational only — not financial advice. Private paths and secrets stay off this page.

---

## 0. What I changed

I failed v1 by stacking complexity: regime models, LLM trade proposals, multi-tier risk, options ladders, weekly Sonnet blogs. Cleverness beat compounding.

I start v2 from boring blocks:

- Quality broad-market and dividend ETFs
- Buy weakness (that ticker red today), hold
- Measure everything against **my actual paper capital**
- Expand only after simple scenarios earn the right

No LLM in the trading loop right now. I want to earn that back later — see [Method](/research).

---

## 1. Runtime topology

| Piece | Role |
| --- | --- |
| **Main service** | `python -m trader.main` — blocking scheduler |
| **Schedule** | **10:30 ET** and **15:30 ET**, Mon–Fri, US RTH only |
| **Log viewer** | Private FastAPI UI for journal + positions (not this public site) |

Configuration and API keys live in a host-managed environment file — never in git.

| Artifact | Typical relative path |
| --- | --- |
| SQLite journal | `data/logs/trades.db` |
| JSONL mirror | `data/logs/journal.jsonl` |
| Fund universe | `config/funds.yaml` |

---

## 2. Strategy (rule A)

On each scan:

1. Load the fixed ETF universe (three sleeves).
2. For each ticker, compute **its** day return (prior close → last).
3. **Green → skip.**
4. **Red** and sleeve **underweight** vs targets (≈50% / 25% / 25%) → candidate.
5. Prefer sleeve **primary** if red (VOO / SCHD / QQQM); else first red alternate.
6. Skip if that sleeve already received a **placed buy** earlier the same session.
7. Market-buy a notional slice of equity (default **5%**, cash-capped).
8. **No auto-sell** in software.

Afternoon scan exists so a book that was green at 10:30 can still buy if it turns red later.

```text
schedule (10:30 / 15:30 ET)
  → for each fund
      → day_return(ticker)
      → if green: HOLD
      → if red and sleeve underweight and not bought today: market BUY
  → journal
```

---

## 3. Fund sleeves

Documented universe (primaries first):

| Sleeve | Target | Primary | Alternates |
| --- | --- | --- | --- |
| Core | ~50% | VOO | VTI, IVV, SPYM, VT |
| Dividend | ~25% | SCHD | VYM, VIG, DGRO, JEPI |
| Growth | ~25% | QQQM | QQQ, SCHG, VUG, VGT |

Sleeve weight = sum of market value of that sleeve’s tickers ÷ equity.

---

## 4. What is intentionally missing

- Regime / Haiku pre-brain
- Claude trader JSON loop
- Options execution and profit ladders
- Soft risk policies and YOLO profiles
- Weekly Sonnet blog generator
- Local LLM / Open WebUI / assistant gateways on the trading host

Those were v1 complexity. Code from that era can live in an archive for archaeology; it is not on the live import path.

---

## 5. Journal and operator tools

Every HOLD/BUY writes a journal row (rationale, day return, sleeve, execute status). The private dashboard shows cycles and open positions and links out to the Alpaca paper UI. Public pages here stay educational and redacted.

---

## 6. Method going forward

1. Keep the live rule set tiny.
2. Backtest / paper-validate simple scenarios against the same capital the bot actually uses.
3. Add the next knob only when the scoreboard (paper equity + journal) justifies it.

**Later (not built):** mutate rule variants, score on paper fitness, keep winners. Details on **Method**.
