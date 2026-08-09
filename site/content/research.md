# Method

Paper postmortem + where this is headed. **Not financial advice.**

---

## v1 — what it was

A full “desk in software”: regime model, fat snapshots, Haiku BUY/SELL/HOLD, heavy risk stack, **lots of options**, weekly Sonnet blogs. Clever. Brittle. Hard to attribute P&amp;L.

![v1 pipeline](/research-examples/v1-pipeline.svg)

## v1 — paper results

Apr 29 → Jul 30, 2026 · **$100k → $84,195 (−15.8%)** · peak ~$101.6k · max DD ~17% · 4,651 cycles · 439 orders · BUY/SELL/HOLD/HALT **258 / 287 / 3,623 / 483**. Buys were mostly **long_call** under `yolo_options`.

![equity](/research-examples/v1-equity-curve.svg)

![actions](/research-examples/v1-actions.svg)

![buy mix](/research-examples/v1-buy-strategies.svg)

![top symbols](/research-examples/v1-top-symbols.svg)

**Why it failed:** complexity beat evidence; activity ≠ edge; options + LLM discretion stacked noise; ops cost was real. Missed lesson: boring funds, buy weakness, hold.

---

## v2 — now

![v2 loop](/research-examples/v2-loop.svg)

Same paper book (~$84k left). Rule: quality ETF **red today** + sleeve underweight → small buy → **hold**. No LLM. Scans 10:30 & 15:30 ET. One buy/sleeve/day. Expand only what the paper scoreboard earns. [Architecture](/architecture) · [Flow](/flow).

---

## Future — open

Not another LLM desk. [MarI/O](https://www.youtube.com/watch?v=qv6UVOQ0F44)-style **evolutionary search**: mutate simple rule variants, score on paper fitness, keep winners. Algorithm undecided. v2 is the **seed** a mutator has to beat — not built yet.

![evolve loop](/research-examples/v-future-evolve.svg)
