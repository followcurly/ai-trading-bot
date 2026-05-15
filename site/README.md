# Public architecture site (`site/`)

This folder is a **standalone Next.js (App Router)** app that ships curated, redacted **architecture prose** and an **interactive Mermaid flow** (pan/zoom, minimap thumbnail, click-to-panel). It is safe to expose on the public internet: **no API keys**, no private host paths, and a **`check:public` blocklist** runs on every `npm run build`.

## Vercel

1. **Root Directory:** set to **`site`** (not the monorepo root) so installs/builds only see Node tooling.
2. **Framework preset:** Next.js (auto-detected).
3. **Environment variables:** none required for the static content. Optional: `NEXT_PUBLIC_SITE_URL` with your canonical URL (e.g. `https://your-domain.com`) for Open Graph / metadata base.
4. **First import:** Vercel dashboard → *Add New Project* → import the Git repo → **Root Directory = `site`** → Deploy.
5. **Later deploys:** push to the connected branch, or use the Vercel CLI / MCP from this directory.

## Scripts

| Command | Purpose |
| --- | --- |
| `npm run dev` | Local dev server |
| `npm run check:public` | Blocklist scan over curated paths: internal absolute paths, CGNAT-range IPv4, VPN/keypair keywords, sensitive env names, etc. |
| `npm run build` | Runs `check:public` then production build |

## Content

- `content/architecture.md` — sanitized long-form doc (do **not** symlink private `docs/ARCHITECTURE.md`).
- `content/flow.mmd` — Mermaid source (no custom `classDef` in v1; includes dashed **regime → snapshot builder** edge for benchmark slices).
- `content/flow-panels.json` — node id → `{ title, body, module }` for the click panel.

## LinkedIn blurb (copy/paste)

> I published a small Next.js site with an interactive Mermaid diagram (pan/zoom + node notes) and a redacted architecture write-up for my AI-assisted **research** trading stack: external feeds → per-cycle pipeline → sinks + weekly reporting. Educational only — not live trading or signals.  
> *(Replace with your real Vercel URL after deploy.)*

## Disclaimer

Everything here is **educational documentation**, not financial advice, not an offer of services, and **not** wired to any brokerage or model keys.
