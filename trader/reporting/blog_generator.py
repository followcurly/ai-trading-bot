"""Generate human-facing weekly blog posts (funny, non-technical) via Claude.

Posts live as Markdown files under ``data/reports/`` (override with the
``DATA_REPORTS_DIR`` env var) and are named ``blog-<slug>.md`` where ``<slug>``
is conventionally ``YYYY-MM-DD[-suffix]``. Each post may include an optional
YAML frontmatter block to control how it renders on the dashboard:

```markdown
---
title: A Robot Walks Into a Stock Market
date: 2026-05-11
kind: launch          # launch | weekly | note
subtitle: Inception edition
draft: false
featured: true
charts: false         # opt out of the equity/decisions chart strip
excerpt: Optional override for the index card excerpt.
tags: [launch, overview]
---
```

Posts without frontmatter still work — the title is extracted from the first
``# Heading`` line and the date from the slug, exactly as before.

Seed posts (e.g. the original launch announcement) live under
``trader/reporting/blog_seeds/`` and are copied into ``data/reports/`` on demand
by :func:`ensure_seed_posts`. Existing files in the reports dir are never
overwritten, so manual edits survive.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from trader.config import DB_PATH, REPO_ROOT

_BLOG_PREFIX = "blog-"
_MAX_EXCERPT_CHARS = 280
_VALID_KINDS = {"launch", "weekly", "note"}
_DEFAULT_KIND = "weekly"

# Legacy constants kept so out-of-tree callers (or older deploys) that import
# them by name continue to work. New code should not depend on these.
LAUNCH_POST_SLUG = "2026-05-11-launch"
LAUNCH_POST_FILENAME = f"{_BLOG_PREFIX}{LAUNCH_POST_SLUG}.md"


def reports_dir() -> Path:
    p = Path(os.getenv("DATA_REPORTS_DIR", str(REPO_ROOT / "data" / "reports")))
    p.mkdir(parents=True, exist_ok=True)
    return p


def seeds_dir() -> Path:
    """Directory of seed (committed) blog posts that ship with the codebase."""
    override = os.getenv("BLOG_SEEDS_DIR")
    if override:
        return Path(override)
    return Path(__file__).resolve().parent / "blog_seeds"


# ---------------------------------------------------------------------------
# Seed posts — copy committed Markdown files into the live reports dir
# ---------------------------------------------------------------------------

def ensure_seed_posts(rd: Path | None = None, *, force: bool = False) -> list[Path]:
    """Copy any seed posts under ``seeds_dir()`` into the reports directory.

    By default only fills in *missing* destinations so manual edits to live posts
    are never clobbered. Set ``force=True`` (or ``BLOG_SEED_FORCE=true`` in the
    environment) to overwrite. Returns the list of destination paths that were
    written this call.
    """
    rd = rd or reports_dir()
    sd = seeds_dir()
    if not sd.is_dir():
        return []

    if not force:
        force = os.getenv("BLOG_SEED_FORCE", "").lower() in ("1", "true", "yes")

    written: list[Path] = []
    for src in sorted(sd.glob("*.md")):
        slug = src.stem
        dest = rd / f"{_BLOG_PREFIX}{slug}.md"
        if dest.exists() and not force:
            continue
        shutil.copyfile(src, dest)
        written.append(dest)
    return written


def ensure_launch_post() -> Path | None:
    """Backwards-compatible alias for the old single-file seeder.

    Kept so ``trader.weekly_review`` (and any external scripts that imported the
    name) keep working. Returns the launch post path if the seed exists, else
    ``None``.
    """
    written = ensure_seed_posts()
    rd = reports_dir()
    p = rd / LAUNCH_POST_FILENAME
    return p if p.is_file() else (written[0] if written else None)


# ---------------------------------------------------------------------------
# Claude prompt builder
# ---------------------------------------------------------------------------

def human_blog_prompt_from_pack(pack: dict[str, Any]) -> str:
    """Build a Claude prompt that produces a funny, non-technical weekly blog post."""
    stats = pack.get("sqlite_stats") or {}
    by_action = stats.get("by_action") or {}
    eq_first = stats.get("equity_first")
    eq_last = stats.get("equity_last")

    eq_line = ""
    if eq_first and eq_last:
        delta = float(eq_last) - float(eq_first)
        sign = "+" if delta >= 0 else ""
        eq_line = (
            f"Starting equity this week: ${float(eq_first):,.2f}. "
            f"Ending equity: ${float(eq_last):,.2f}. "
            f"Change: {sign}${delta:,.2f}."
        )
    else:
        eq_line = "Equity data not available for this period."

    top_symbols = [s["symbol"] for s in (stats.get("top_symbols") or [])[:6]]
    total_cycles = stats.get("rows_since_cutoff", 0)
    buys = by_action.get("BUY", 0)
    sells = by_action.get("SELL", 0)
    holds = by_action.get("HOLD", 0)
    halts = by_action.get("HALT", 0)

    alpaca = pack.get("alpaca_filled_summary") or {}
    filled = alpaca.get("filled_order_count", 0)

    snap = pack.get("bot_operating_snapshot") or {}
    snap_line = ""
    if isinstance(snap, dict) and snap:
        tiers = snap.get("options_size_tiers") or []
        tiers_s = ", ".join(
            f"{t.get('threshold')}→{t.get('max_contracts')}c" for t in tiers if isinstance(t, dict)
        ) or "(tiers off / floor only)"
        snap_line = (
            f"Bot tuning this run: screener cap {snap.get('watchlist_screener_size')} names; "
            f"inject open underlyings={snap.get('watchlist_include_open_positions')}; "
            f"option caps floor={snap.get('max_options_contracts_floor')} hard={snap.get('max_options_contracts_hard')} "
            f"tiers [{tiers_s}]; trail giveback {snap.get('trail_giveback_pct')}. "
            f"Journal lines may show option_qty / conviction_contract_cap on option trades."
        )

    entries_snippet = ""
    entries = pack.get("entries_for_prompt") or []
    if entries:
        sample = entries[-10:]
        entries_snippet = json.dumps(sample, indent=2, default=str)

    return f"""You are writing a **weekly public blog post** about an automated AI trading bot.

AUDIENCE: Non-technical general public. No finance or programming knowledge assumed.
VOICE: Third-person observer — a sharp, witty narrator covering "the bot" from the outside.
 Write about it the way a sports commentator or a tech journalist would: with personality,
 dry humor, and genuine curiosity. Never write from the bot's point of view. Never use "I"
 as the bot. Refer to it as "the bot", "the system", or "it".
FORMAT: Markdown. Use ## headings. Use emoji where they add punch (don't overdo it).
 Aim for ~600–900 words. Punchy sentences. Short paragraphs.
PURPOSE: Tell the story of the week — what the bot did, what the numbers look like, what
 patterns are showing up, what's being adjusted heading into next week. Make data interesting
 to someone who has never bought a stock in their life.

ACTUAL DATA FROM THIS WEEK:
- Period: last {pack.get("period_days", 7)} days
- Total decision cycles run: {total_cycles}
- BUY decisions: {buys} · SELL decisions: {sells} · HOLD decisions: {holds} · HALT (risk engine blocked): {halts}
- Orders filled on Alpaca paper account: {filled}
- Most-watched symbols this week: {", ".join(top_symbols) if top_symbols else "none recorded yet"}
- {eq_line}
{f"- {snap_line}" if snap_line else "- (operating snapshot unavailable)"}

RECENT DECISION SAMPLE (last 10 log entries — do NOT invent trades not in this data):
{entries_snippet if entries_snippet else "(no entries recorded yet this period)"}

RULES:
- Do NOT invent dollar P&L figures not supported by the equity data above.
- If it was a slow week or data is thin, be honest about it — slow weeks can be funny too.
- Explain finance concepts (RSI, stop-loss, bracket order, etc.) using plain analogies.
 Example: "a stop-loss is basically the bot saying — if this goes any lower, get me out."
- End with a "## What's Next" section: 2–3 things to watch or expect next week.
- Do NOT use the word "delve". Do NOT end with hollow motivational phrases.
- No footnotes or disclaimers — those appear automatically below the post.
- The blog publishes every Sunday. Write accordingly.

Produce the blog post now. Start with a `# Title` line (make it punchy, no clickbait).
Second line: `*Week of [date] · [one-line summary]*`
"""


# ---------------------------------------------------------------------------
# Frontmatter + post parsing helpers
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def _split_frontmatter(md_text: str) -> tuple[dict[str, Any], str]:
    """Return (meta, body). If no frontmatter present, meta is empty."""
    m = _FRONTMATTER_RE.match(md_text)
    if not m:
        return {}, md_text
    raw = m.group(1)
    body = md_text[m.end():]
    try:
        loaded = yaml.safe_load(raw) or {}
        if not isinstance(loaded, dict):
            return {}, md_text
        return loaded, body
    except yaml.YAMLError:
        return {}, md_text


def _coerce_kind(value: Any) -> str:
    if isinstance(value, str) and value.lower() in _VALID_KINDS:
        return value.lower()
    return _DEFAULT_KIND


def _coerce_date(value: Any, slug: str) -> str:
    """Stringify a YAML date value or fall back to the date in the slug."""
    if value is None:
        return _date_from_slug(slug)
    if isinstance(value, (datetime,)):
        return value.date().isoformat()
    s = str(value).strip()
    return s or _date_from_slug(slug)


def _slug_from_path(p: Path) -> str:
    """blog-2026-05-11-foo.md → 2026-05-11-foo"""
    name = p.stem
    if name.startswith(_BLOG_PREFIX):
        return name[len(_BLOG_PREFIX):]
    return name


def _date_from_slug(slug: str) -> str:
    """Extract YYYY-MM-DD from slug; fall back to empty string."""
    m = re.match(r"(\d{4}-\d{2}-\d{2})", slug)
    return m.group(1) if m else ""


def _excerpt_from_body(body_md: str, override: str | None = None) -> str:
    if override:
        s = str(override).strip()
        if len(s) > _MAX_EXCERPT_CHARS:
            return s[:_MAX_EXCERPT_CHARS] + "…"
        return s
    for line in body_md.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#") or line.startswith("*") or line.startswith("---"):
            continue
        if line.startswith("<"):
            continue
        excerpt = line[:_MAX_EXCERPT_CHARS]
        if len(line) > _MAX_EXCERPT_CHARS:
            excerpt += "…"
        return excerpt
    return ""


def parse_blog_post(md_text: str, slug: str) -> dict[str, Any]:
    """Extract metadata + html_body from Markdown text.

    Frontmatter (if present) takes precedence over slug-derived defaults and
    over the legacy "first ``# Heading`` line is the title" convention.
    """
    import markdown as md_lib

    meta, body_md = _split_frontmatter(md_text)

    title = str(meta.get("title") or "").strip() or slug
    body_start_idx = 0
    if not meta.get("title"):
        lines = body_md.splitlines()
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("# "):
                title = stripped[2:].strip()
                body_start_idx = i + 1
                break
        body_md = "\n".join(lines[body_start_idx:]).strip()
    else:
        body_md = body_md.lstrip("\n")

    html_body = md_lib.markdown(
        body_md,
        extensions=["extra", "sane_lists", "nl2br", "fenced_code"],
        output_format="html",
    )

    excerpt = _excerpt_from_body(body_md, override=meta.get("excerpt"))
    tags = meta.get("tags") or []
    if not isinstance(tags, list):
        tags = [str(tags)]

    return {
        "slug": slug,
        "title": title,
        "date": _coerce_date(meta.get("date"), slug),
        "kind": _coerce_kind(meta.get("kind")),
        "subtitle": str(meta.get("subtitle") or "").strip(),
        "excerpt": excerpt,
        "draft": bool(meta.get("draft", False)),
        "featured": bool(meta.get("featured", False)),
        "charts": bool(meta.get("charts", True)),
        "tags": [str(t) for t in tags],
        "html_body": html_body,
    }


def list_blog_posts(rd: Path | None = None, *, include_drafts: bool = False) -> list[dict[str, Any]]:
    """Return all published blog posts sorted newest-first.

    Drops ``html_body`` (use :func:`get_blog_post` for the full post) and skips
    posts marked ``draft: true`` unless ``include_drafts=True``.
    """
    rd = rd or reports_dir()
    ensure_seed_posts(rd)
    posts: list[dict[str, Any]] = []
    for p in rd.glob(f"{_BLOG_PREFIX}*.md"):
        slug = _slug_from_path(p)
        try:
            md_text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        post = parse_blog_post(md_text, slug)
        if post["draft"] and not include_drafts:
            continue
        posts.append({k: v for k, v in post.items() if k != "html_body"})
    posts.sort(key=lambda p: (p["date"], p["slug"]), reverse=True)
    return posts


def get_blog_post(slug: str, rd: Path | None = None) -> dict[str, Any] | None:
    """Return a single parsed blog post by slug, or ``None`` if not found."""
    rd = rd or reports_dir()
    ensure_seed_posts(rd)
    p = rd / f"{_BLOG_PREFIX}{slug}.md"
    if not p.is_file():
        return None
    try:
        md_text = p.read_text(encoding="utf-8")
    except OSError:
        return None
    return parse_blog_post(md_text, slug)


# ---------------------------------------------------------------------------
# Write artifacts
# ---------------------------------------------------------------------------

def generate_blog_charts(pack: dict[str, Any], slug: str) -> dict[str, str]:
    """Generate SVG charts from the weekly data pack. Returns {chart_name: svg_string}."""
    from trader.reporting.chart_svg import (
        decision_breakdown_svg,
        equity_curve_svg,
        load_equity_series,
        top_symbols_svg,
    )

    stats   = pack.get("sqlite_stats") or {}
    cutoff  = pack.get("cutoff_utc") or ""
    charts: dict[str, str] = {}

    equity_rows = load_equity_series(DB_PATH, cutoff)
    charts["equity_curve"] = equity_curve_svg(equity_rows)

    by_action = stats.get("by_action") or {}
    charts["decisions"] = decision_breakdown_svg(by_action)

    top_symbols = stats.get("top_symbols") or []
    charts["top_symbols"] = top_symbols_svg(top_symbols)

    return charts


def write_human_blog(pack: dict[str, Any], blog_markdown: str) -> dict[str, str]:
    """Write the weekly human blog post + SVG charts sidecar to data/reports/."""
    rd  = reports_dir()
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = day
    fname        = f"{_BLOG_PREFIX}{slug}.md"
    charts_fname = f"{_BLOG_PREFIX}{slug}.charts.json"

    charts = generate_blog_charts(pack, slug)

    paths = {
        "blog_md":        str(rd / fname),
        "blog_charts":    str(rd / charts_fname),
        "latest_blog_md": str(rd / "LATEST_BLOG.md"),
    }
    (rd / fname).write_text(blog_markdown, encoding="utf-8")
    (rd / charts_fname).write_text(json.dumps(charts), encoding="utf-8")
    (rd / "LATEST_BLOG.md").write_text(blog_markdown, encoding="utf-8")
    return paths
