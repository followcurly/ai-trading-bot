#!/usr/bin/env bash
# Quick Alpaca + trader smoke (exit 0 = OK). Uses api.env — no args.
set -euo pipefail
cd /srv/ai-trading-bot
set -a
# shellcheck source=/dev/null
source /root/.openclaw/api.env
set +a
exec .venv/bin/python - <<'PY'
from dotenv import load_dotenv
load_dotenv("/root/.openclaw/api.env")
from trader.data_feed import get_market_snapshot
from trader.alpaca_runtime import trading_client

tc = trading_client()
a = tc.get_account()
assert a.status.name == "ACTIVE"
snap = get_market_snapshot("SPY")
assert snap["price"] > 0
print("trader-health OK:", "equity=", round(float(a.equity), 2), "SPY=", round(snap["price"], 2))
PY
