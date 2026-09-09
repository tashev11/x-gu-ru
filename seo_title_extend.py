#!/usr/bin/env python3
"""Final pass: extend titles shorter than 30 chars by inserting ' и регионе'
before ' | СЕО ГУРУ'. Also update og:title, twitter:title and CollectionPage
JSON-LD name where the same text appears.

The healthcheck requires 30 <= len(title) <= 75. Adding ' и регионе' (10 chars)
keeps the longest cities/services comfortably under 75.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path("/var/www/x-gu.ru/current")
SUFFIX = " | СЕО ГУРУ"
INSERT = " и регионе"

# Match the title and capture its inner text
re_title = re.compile(r"<title>([^<]*?)</title>")
# Match og:title / twitter:title meta tags by content
re_og_title = re.compile(r'(<meta[^>]+property="og:title"[^>]+content=")([^"]+)(")')
re_tw_title = re.compile(r'(<meta[^>]+name="twitter:title"[^>]+content=")([^"]+)(")')
# CollectionPage JSON-LD name (city hub)
re_jsonld_name = re.compile(r'("@type":"CollectionPage","name":")([^"]+)(")')


def extend_title(title: str) -> str:
    if SUFFIX not in title:
        return title
    head, _, _ = title.partition(SUFFIX)
    if INSERT in head:
        return title  # already extended
    return f"{head}{INSERT}{SUFFIX}"


def patch_file(path: Path) -> int:
    text = path.read_text(encoding="utf-8")
    m = re_title.search(text)
    if not m:
        return 0
    old_title = m.group(1).strip()
    if len(old_title) >= 30:
        return 0
    new_title = extend_title(old_title)
    if new_title == old_title:
        return 0

    text = text.replace(f"<title>{old_title}</title>", f"<title>{new_title}</title>", 1)

    def og_repl(match: re.Match) -> str:
        return match.group(1) + extend_title(match.group(2)) + match.group(3)

    text = re_og_title.sub(og_repl, text, count=1)
    text = re_tw_title.sub(og_repl, text, count=1)

    def jsonld_repl(match: re.Match) -> str:
        return match.group(1) + extend_title(match.group(2)) + match.group(3)

    text = re_jsonld_name.sub(jsonld_repl, text, count=1)

    path.write_text(text, encoding="utf-8")
    return 1


def main() -> int:
    if not ROOT.is_dir():
        print(f"Root not found: {ROOT}", file=sys.stderr)
        return 1
    patched = 0
    scanned = 0
    for html in ROOT.rglob("index.html"):
        scanned += 1
        try:
            patched += patch_file(html)
        except Exception as e:
            print(f"  ERROR {html}: {e}", file=sys.stderr)
        if scanned % 5000 == 0:
            print(f"  ... scanned={scanned} patched={patched}", flush=True)
    print(f"[DONE] scanned={scanned} patched={patched}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
