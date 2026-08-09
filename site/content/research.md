# Method — what v1 was, what the paper book said, what we do now

Educational postmortem. **Not financial advice.** Numbers below are from a **paper** (simulated) brokerage journal, Apr 29 → Jul 30, 2026.

---

## The v1 machine

v1 tried to automate “a whole desk”:

1. **Regime pre-brain** — a small model called the tape (bull/bear/conviction) from cross-asset inputs  
2. **Dynamic watchlist** — screener + hedges + whatever was already held  
3. **Fat snapshots** — bars, indicators, macro, news, options ATM context, data-quality flags  
4. **Haiku trader** — JSON BUY/SELL/HOLD per symbol  
5. **Risk engine** — PDT, sector caps, cooldowns, soft policies, overnight rules  
6. **Executor** — stock brackets plus **lots of single-leg options**  
7. **Position health** — trails, ladders, hard stops on a timer  
8. **Weekly Sonnet** — blog-shaped postmortems  

It looked impressive on a diagram. Day to day it was brittle, expensive (model calls), and hard to attribute P&amp;L to any one idea.

![v1 pipeline stages](/research-examples/v1-pipeline.svg)

---

## Paper results (v1 closed book)

Starting capital **$100,000** paper. Ending equity **$84,195** (−**$15,805**, **−15.8%**). Peak briefly cleared **~$101.6k**; max peak-to-floor drawdown in the journal was about **17%**.

| Metric | Value |
| --- | --- |
| Period | Apr 29 – Jul 30, 2026 (~3 months) |
| Journal cycles | 4,651 |
| Unique symbols touched | 119 |
| Orders placed | 439 |
| Order errors | 9 |
| BUY / SELL / HOLD / HALT | 258 / 287 / 3,623 / 483 |

![v1 paper equity curve](/research-examples/v1-equity-curve.svg)

Most journal rows were **HOLD** or **HALT** — a lot of machinery for relatively few decisive trades. When it did buy, the proposed strategies skewed hard to **options**:

| BUY strategy (proposed) | Count |
| --- | --- |
| long_call | 218 |
| long_put | 30 |
| long_stock | 10 |

![v1 actions](/research-examples/v1-actions.svg)

![v1 buy strategies](/research-examples/v1-buy-strategies.svg)

Top symbols by **orders placed** were the usual high-beta / meme-adjacent tape the aggressive profile hunted (AMD, TSLL, NVDA, TQQQ, META, …) — not a patient fund core.

![v1 top symbols](/research-examples/v1-top-symbols.svg)

Profiles in the journal: a large stretch ran as **`yolo_options`** (aggressive options-only presets). That matched the complexity and the drawdown shape.

---

## Why that counts as a failure

Not because “−16% in three months is impossible to recover.” Paper drawdowns happen. It counts as a failure because:

- **Complexity outran evidence** — each new module was justified by cleverness, not by a simple scenario that already worked  
- **Activity ≠ edge** — thousands of cycles, enrichment layers, and model tokens did not produce a trustworthy process  
- **Options + LLM discretion** stacked uncertainty on uncertainty  
- **Ops cost** was real: keys, crons, weekly model reviews, dashboards for a stack nobody could hold in their head  

The honest line: **I did not trust time-tested methods long enough.** Broad funds, buying weakness, and holding are boring. Boring was the missed lesson.

---

## What v2 is instead

![v2 simplified loop](/research-examples/v2-loop.svg)

A small hypothesis on the **same continuing paper book** (starting from the ~$84k left after v1 — no fairy-tale reset to $100k):

> If a listed quality ETF is **red today**, and that **sleeve** is still under target weight, buy a small slice and **hold**.

- No LLM in the loop  
- Scans **10:30** and **15:30 ET** so late-day red still matters  
- One buy per sleeve per day max  
- Expand only after simple scenarios are backtested / validated against this capital  

Details live on [Architecture](/architecture) and [Flow](/flow).

---

## How expansion is supposed to work

1. Keep the live rule set tiny  
2. Backtest **simple** scenarios against the same capital story  
3. Promote only what survives the paper book + journal  
4. Refuse modules that exist to look sophisticated  

New tools (replay charts, scenario runners, promotion checklists) have to earn a seat by helping those four steps — not by resurrecting the v1 brain.

---

## What this page is not

Not a signal service. Not live advice. Not a claim that “buy red ETFs” prints money. It is a public lab notebook: v1 left receipts (−15.8% paper, options-heavy), and v2 is choosing a narrower path on purpose.
