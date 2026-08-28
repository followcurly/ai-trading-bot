# AI trading bot — agent notes

- Live codespace on CT 202 (`ai-stack`). Open ONLY `/srv/ai-trading-bot` (hosts `ai-stack` / `ai-stack-lan`).
- No Mac clone by design. Nightly Proxmox vzdump of CT 202 → TrueNAS is the primary backup.
- Services: `trading-bot.service`, `trading-logview.service` (port 8788). Prefer systemctl for restarts; avoid breaking paper trading carelessly.
- GitHub: `followcurly/ai-trading-bot`. Keep `.venv/`, `data/`, `__pycache__/` out of commits.
- Optional portable clones of other apps go under `/srv/code/<repo>` on demand — do not bulk-clone client sites here.
