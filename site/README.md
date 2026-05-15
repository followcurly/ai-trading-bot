# Public site (`site/`)

This directory is the **Next.js** front door: landing page, **`/flow`** (interactive Mermaid), and **`/architecture`** (sanitized markdown). It is safe to put on the public internet (no keys; `check:public` runs before build).

**Story, visuals, and “why”** live in the **[root README](../README.md)**. If you are opening this folder to hack UI or content, you only need:

- `npm install` then `npm run dev` from **`site/`**
- Optional deploy: Vercel **Root Directory = `site`**. Set **`NEXT_PUBLIC_SITE_URL`** to **`https://tradebot.followcurly.com`** (production canonical for OG / metadata) once DNS is live.

Everything else (trader, systemd, homelab) is outside this package.
