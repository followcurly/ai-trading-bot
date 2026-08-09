<p align="center">
  <a href="https://github.com/followcurly/ai-trading-bot"><img src="https://img.shields.io/github/stars/followcurly/ai-trading-bot?style=social" alt="GitHub stars" /></a>
  &nbsp;
  <a href="https://github.com/followcurly/ai-trading-bot/commits/main/"><img src="https://img.shields.io/github/last-commit/followcurly/ai-trading-bot?label=last%20commit" alt="Last commit" /></a>
</p>

# Build in public: simple rules, paper capital

I am running a **homelab paper-trading experiment**. v1 tried to be clever (regime models, LLM brains, options ladders) and failed under its own weight. **v2** starts smaller: buy quality ETFs when *that* fund is red, hold, and only expand after simple scenarios earn it against the live paper book.

If you are here to **follow along**, watch the commits, read the public site, and poke holes. Nothing here is financial advice.

---

## Look around (no install required)

| | |
| --- | --- |
| **Live public site** | **[tradebot.followcurly.com](https://tradebot.followcurly.com)** — landing, flow diagram, architecture, method |
| **This repo** | Python trader under `trader/`, docs under `docs/`, Next.js public site under `site/` |

**What you will see on the site**

- **`/`** — why v1 failed and what v2 is testing  
- **`/flow`** — pan/zoom Mermaid: schedule → red check → sleeve weight → buy/hold → journal  
- **`/architecture`** — redacted deep dive of the simplified stack  
- **`/research`** — method: backtest simple scenarios against real paper capital before expanding  

---

## Why I am sharing it

- **Paper first** — same Alpaca paper book is the scoreboard  
- **Rules first** — no LLM in the trading loop  
- **Earn complexity** — add tools only after simple cases prove useful  
- **Receipts** — journal + private operator dashboard  

---

## Follow the work

| Where | Link |
| --- | --- |
| **GitHub** | [github.com/followcurly/ai-trading-bot](https://github.com/followcurly/ai-trading-bot) |
| **LinkedIn** | [linkedin.com/in/diazebas](https://www.linkedin.com/in/diazebas/) |

---

## Homelab operator notes (optional)

If you are **me** on a new machine: **[WORKSPACE_START_HERE.md](WORKSPACE_START_HERE.md)** and **[docs/TRADING_BOT.md](docs/TRADING_BOT.md)**. Everyone else can ignore that block.
