"""Blog chrome configuration.

The dashboard's blog index, post pages, and nav label all used to be hardcoded
copy embedded directly in Jinja templates. This module is the single source of
truth for that copy so it can be tweaked without editing templates and so the
"Friday/Sunday" cadence string never drifts again.

Every value can be overridden via an environment variable; defaults match the
historical hardcoded strings so existing deploys see no behavior change.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env(name: str, default: str) -> str:
    """Return env override stripped of surrounding whitespace, or the default."""
    value = os.getenv(name)
    if value is None:
        return default
    stripped = value.strip()
    return stripped if stripped else default


@dataclass(frozen=True)
class BlogConfig:
    """Strings rendered around the bot's public blog."""

    nav_label: str
    hero_title: str
    hero_subtitle: str
    cadence: str
    default_subtitle: str
    disclaimer: str
    empty_state_title: str
    empty_state_hint: str
    footer_note: str

    @classmethod
    def from_env(cls) -> "BlogConfig":
        cadence = _env("BLOG_CADENCE", "Sunday")
        return cls(
            nav_label=_env("BLOG_NAV_LABEL", "📓 Blog"),
            hero_title=_env("BLOG_HERO_TITLE", "📓 The Bot's Journal"),
            hero_subtitle=_env(
                "BLOG_HERO_SUBTITLE",
                f"Every {cadence}, the AI writes up its week — what it noticed, "
                "what it changed, and what confused it. Human-readable, "
                "occasionally funny, always honest.",
            ),
            cadence=cadence,
            default_subtitle=_env("BLOG_DEFAULT_SUBTITLE", "Weekly recap"),
            disclaimer=_env(
                "BLOG_DISCLAIMER",
                "🤖 This post is generated automatically every "
                f"{cadence} by the same weekly review process that feeds the "
                "bot's strategy. All trades are on a paper (simulated) account "
                "with $100,000 of simulated capital. Nothing here is financial "
                "advice.",
            ),
            empty_state_title=_env("BLOG_EMPTY_TITLE", "No blog posts yet."),
            empty_state_hint=_env(
                "BLOG_EMPTY_HINT",
                "Run `python -m trader.weekly_review` to generate the first one, "
                "or check back after launch.",
            ),
            footer_note=_env(
                "BLOG_FOOTER_NOTE",
                f"Posts are generated automatically every {cadence} by the same "
                "Claude model that makes trading decisions. They reflect actual "
                "bot behavior — no edits, no spin. Nothing here is financial "
                "advice.",
            ),
        )

    def to_template_context(self) -> dict[str, str]:
        """Shape compatible with Jinja's ``blog`` namespace in the templates."""
        return {
            "nav_label": self.nav_label,
            "hero_title": self.hero_title,
            "hero_subtitle": self.hero_subtitle,
            "cadence": self.cadence,
            "default_subtitle": self.default_subtitle,
            "disclaimer": self.disclaimer,
            "empty_state_title": self.empty_state_title,
            "empty_state_hint": self.empty_state_hint,
            "footer_note": self.footer_note,
        }
