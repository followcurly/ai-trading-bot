# Method

I am writing this as a lab notebook — paper trading only. **Not financial advice.**

---

## I tried to build a desk

I wanted a machine that could think like a trading desk: regime calls, fat market snapshots, an LLM proposing BUY/SELL/HOLD, a thick risk layer, options ladders, even weekly model-written postmortems.

I built that. It looked impressive on a diagram.

![v1 pipeline](/research-examples/v1-pipeline.svg)

## I ended up here

On paper, from Apr 29 to Jul 30, 2026, I watched **$100k become $84,195 (−15.8%)**. Peak briefly cleared ~$101.6k. Max drawdown was about **17%**. Thousands of cycles, mostly HOLD/HALT, and when I did buy it was usually **long calls** under an aggressive options profile.

![equity](/research-examples/v1-equity-curve.svg)

![actions](/research-examples/v1-actions.svg)

![buy mix](/research-examples/v1-buy-strategies.svg)

I do not treat the loss itself as the whole failure. The failure was **complexity without a trustworthy edge** — activity that felt smart, P&amp;L I could not attribute, and ops cost I could feel. I never gave boring methods enough room: quality funds, buy weakness, hold.

## I am starting here

I kept the same paper book (~$84k left — no fairy-tale reset). I stripped the LLM out of the loop. Now I scan quality ETFs twice a day. If a fund is **red today** and its sleeve is underweight, I buy a small slice and **hold**.

![v2 loop](/research-examples/v2-loop.svg)

Details live on [Architecture](/architecture) and [Flow](/flow).

## What I measure

I am keeping the scoreboard simple so I cannot kid myself:

- **Paper equity** vs the ~$84k starting line for v2  
- **Drawdown** from peak  
- **Buys placed** (and whether I stick to one per sleeve per day)  
- **Sleeve weights** vs the 50 / 25 / 25 targets  
- **Journal completeness** — every scan leaves a receipt  

If those numbers do not improve under a rule I can explain in one sentence, I do not get to add machinery.

## Where I hope to go

I want a [MarI/O](https://www.youtube.com/watch?v=qv6UVOQ0F44)-style loop later: mutate simple rule variants, score them on paper fitness, keep winners. v2 is the **seed** that loop has to beat — I have not built the mutator yet.

![evolve loop](/research-examples/v-future-evolve.svg)

As I iterate from a boring baseline that I trust, I also want to **learn the LLM aspect again** — not as a day-one brain that places options, but as something I earn the right to reintroduce once the paper book and the fitness loop teach me what “better” looks like.
