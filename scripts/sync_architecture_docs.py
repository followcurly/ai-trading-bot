#!/usr/bin/env python3
"""Sync marked sections from docs/ARCHITECTURE.md into site/content/architecture.md."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CANONICAL = REPO / "docs" / "ARCHITECTURE.md"
PUBLIC = REPO / "site" / "content" / "architecture.md"

MARKERS = ("profiles", "research")


def _extract_section(md: str, name: str) -> str | None:
    start = f"<!-- SYNC:{name} -->"
    end = f"<!-- /SYNC:{name} -->"
    if start not in md or end not in md:
        return None
    pat = re.compile(
        re.escape(start) + r"(.*?)" + re.escape(end),
        re.DOTALL,
    )
    m = pat.search(md)
    return m.group(1).strip() if m else None


def _inject(public: str, name: str, body: str) -> str:
    start = f"<!-- SYNC:{name} -->"
    end = f"<!-- /SYNC:{name} -->"
    block = f"{start}\n{body}\n{end}"
    if start in public and end in public:
        pat = re.compile(
            re.escape(start) + r".*?" + re.escape(end),
            re.DOTALL,
        )
        return pat.sub(block, public, count=1)
    return public.rstrip() + "\n\n" + block + "\n"


def sync(*, check: bool = False) -> int:
    canon = CANONICAL.read_text(encoding="utf-8")
    public = PUBLIC.read_text(encoding="utf-8")
    updated = public
    missing: list[str] = []
    for name in MARKERS:
        body = _extract_section(canon, name)
        if body is None:
            missing.append(name)
            continue
        updated = _inject(updated, name, body)
    if missing:
        print(f"Missing SYNC blocks in {CANONICAL}: {missing}", file=sys.stderr)
        return 1
    if check:
        if updated != public:
            print("site/content/architecture.md is out of sync with docs/ARCHITECTURE.md")
            return 1
        print("architecture docs in sync")
        return 0
    PUBLIC.write_text(updated, encoding="utf-8")
    print(f"Wrote {PUBLIC}")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    raise SystemExit(sync(check=args.check))


if __name__ == "__main__":
    main()
