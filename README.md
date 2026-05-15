# AI Trading Bot — public site

Public, **sanitized** architecture overview and interactive Mermaid flow for an AI-assisted equity/options research stack.

> Educational documentation only. **Not** financial advice, **not** a live trading interface, **not** an offer to provide trading services. No brokerage accounts, prompts, model keys, or live order pathways live in this repository.

## Pages

- `/` — pitch + disclaimer
- `/flow` — interactive Mermaid pipeline (pan/zoom, minimap, click-to-panel)
- `/architecture` — long-form prose: runtime topology, per-cycle pipeline, risk engine, executor, journal

## Source layout

```text
.
├── site/          # Next.js (App Router) app — Vercel root directory
│   ├── app/
│   ├── components/
│   ├── content/   # sanitized architecture.md, flow.mmd, flow-panels.json
│   ├── lib/
│   └── scripts/check-public.mjs  # blocklist scan, runs on prebuild
└── README.md
```

## Local development

```bash
cd site
npm install
npm run dev          # http://localhost:3000
npm run check:public # blocklist scan over curated paths
npm run build        # check:public then production build
```

## Deploy

This site deploys to **Vercel** with **Root Directory = `site`**. Push to the default branch and Vercel builds automatically; the build will fail if `check:public` finds internal paths, CGNAT-style IPv4, VPN keywords, or sensitive env names.

## Repository scope

This repository **intentionally contains only the public site code**. The trading bot implementation, model prompts, risk rules, executor, secrets management, and operator viewer live in a private repository and are summarized — never published verbatim — in `site/content/`.
