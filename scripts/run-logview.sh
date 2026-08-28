#!/usr/bin/env bash
set -euo pipefail
cd /srv/ai-trading-bot
# Env vars are loaded by systemd via `EnvironmentFile=/root/.openclaw/api.env`
# in deploy/trading-logview.service. Do NOT `source` it here — bash will try to
# execute any unquoted spaces in values (e.g. EDGAR_USER_AGENT=foo 1.0 bar →
# `1.0: command not found`, exit 127), while systemd's parser handles them.
# Match trader/config.py: bind loopback by default (set TRADE_LOG_HOST=0.0.0.0 to expose on LAN/tailnet).
HOST="${TRADE_LOG_HOST:-127.0.0.1}"
PORT="${TRADE_LOG_PORT:-8788}"
exec /srv/ai-trading-bot/.venv/bin/uvicorn trader.web.app:app --host "$HOST" --port "$PORT"
