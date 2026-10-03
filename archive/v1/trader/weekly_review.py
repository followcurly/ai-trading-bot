"""Weekly Sonnet review: JSONL + SQLite stats → Markdown blog + TRADER_FEEDBACK + LATEST_REVIEW.
Also generates the human-facing blog post (funny, non-technical) on the same run."""

from __future__ import annotations

import os
from pathlib import Path

import anthropic
from dotenv import load_dotenv

# Legacy path; kept so the cron entrypoint picks up the same secrets file the
# systemd units load via EnvironmentFile=. Do not rename without updating
# deploy/trading-*.service in lockstep.
_SHARED_API_ENV = Path("/root/.openclaw/api.env")
if _SHARED_API_ENV.is_file():
    load_dotenv(_SHARED_API_ENV, override=False)

from trader.config import ANTHROPIC_MODEL_SONNET
from trader.reporting.blog_generator import (
    ensure_seed_posts,
    human_blog_prompt_from_pack,
    write_human_blog,
)
from trader.reporting.weekly_report import (
    build_weekly_context_pack,
    sonnet_prompt_from_pack,
    split_sonnet_blog_and_feedback,
    write_weekly_artifacts,
)


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def weekly_review(days: int = 7) -> str:
    # Copy any committed seed posts (e.g. the launch announcement) into the
    # live reports dir on first run. Idempotent and never overwrites edits.
    ensure_seed_posts()

    pack = build_weekly_context_pack(days=days)
    max_tokens = int(os.getenv("WEEKLY_REVIEW_MAX_TOKENS", "6000"))
    client = _client()

    # --- Technical review (existing) ---
    prompt = sonnet_prompt_from_pack(pack)
    response = client.messages.create(
        model=ANTHROPIC_MODEL_SONNET,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    full = response.content[0].text
    blog, feedback = split_sonnet_blog_and_feedback(full)
    paths = write_weekly_artifacts(pack, blog, feedback, ANTHROPIC_MODEL_SONNET)

    # --- Human blog post (new) ---
    human_prompt = human_blog_prompt_from_pack(pack)
    human_resp = client.messages.create(
        model=ANTHROPIC_MODEL_SONNET,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": human_prompt}],
    )
    human_md = human_resp.content[0].text.strip()
    blog_paths = write_human_blog(pack, human_md)
    paths.update(blog_paths)

    print(full)
    print("\n--- artifacts ---\n", paths, sep="")
    return full


if __name__ == "__main__":
    weekly_review()
