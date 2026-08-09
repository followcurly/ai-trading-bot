# Method — start simple, earn complexity

## The v1 failure

The first public iteration of this project treated trading like a software feature factory: regime models, snapshot enrichment piles, an LLM “brain,” layered risk policies, options ladders, and weekly model-written blogs. It was intellectually fun and operationally miserable. Overcomplexity made it hard to trust any single edge, expensive to run, and easy to confuse activity with progress.

The honest postmortem: **I did not trust time-tested methods long enough to let them work.** Broad funds, patience, and buying weakness are boring. Boring is often the point.

## What v2 is testing

A deliberately small hypothesis:

> On days when a listed quality ETF is red, and that sleeve of the portfolio is still under its target weight, buy a small slice with paper capital and hold.

No model decides. No auto-sell. Two scans per session (morning + afternoon) so the book can react if the tape turns red later in the day. Same sleeve is not double-bought in one session.

The scoreboard is the **same Alpaca paper account** the bot trades — not a backtest-only fantasy curve with different assumptions.

## How expansion is supposed to work

1. **Freeze a simple baseline** (current red-day buy-and-hold).
2. **Backtest simple scenarios** against that capital story — same universe, same sizing ideas, clear rules — before shipping more code.
3. **Promote only what survives** contact with the live paper book and the journal.
4. **Refuse** new modules that exist to look sophisticated.

If a future tool appears (replay charts, scenario runners, promotion checklists), it earns a seat by making those four steps easier — not by resurrecting the v1 brain.

## What this page is not

Not a signal service. Not live advice. Not a promise that “buy the dip in ETFs” prints money. It is a public lab notebook for a homelab paper experiment that already burned a version on complexity and is choosing a narrower path on purpose.
