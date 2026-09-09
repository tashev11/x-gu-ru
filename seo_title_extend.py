#!/usr/bin/env python3
"""Extend titles shorter than 30 chars by inserting `` и регионе``.

Safe by default: without ``--apply`` the script only reports how many files
would change. Real writes require an explicit flag.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

DEFAULT_ROOT = Path("/var/www/x-gu.ru/current")
SUFFIX = " | СЕО ГУРУ"
INSERT = " и регионе"

re_title = re.compile(r"<title>([^<]*?)</title>")
re_og_title = re.compile(r'(<meta[^>]+property="og:title"[^>]+content=")([^"]+)(")')
re_tw_title = re.compile(r'(<meta[^>]+name="twitter:title"[^>]+content=")([^"]+)(")')
re_jsonld_name = re.compile(r'("@type":"CollectionPage","name":")([^"]+)(")')


def extend_title(title: str) -> str:
    if SUFFIX not in title:
        return title
    head, _, _ = title.partition(SUFFIX)
    if INSERT in head:
        return title
    return f"{head}{INSERT}{SUFFIX}"


def patched_text(text: str) -> str | None:
    m = re_title.search(text)
    if not m:
        return None
    old_title = m.group(1).strip()
    if len(old_title) >= 30:
        return None
    new_title = extend_title(old_title)
    if new_title == old_title:
        return None

    text = text.replace(f"<title>{old_title}</title>", f"<title>{new_title}</title>", 1)

    def title_repl(match: re.Match[str]) -> str:
        return match.group(1) + extend_title(match.group(2)) + match.group(3)

    text = re_og_title.sub(title_repl, text, count=1)
    text = re_tw_title.sub(title_repl, text, count=1)
    text = re_jsonld_name.sub(title_repl, text, count=1)
    return text


def patch_file(path: Path, apply: bool) -> int:
    text = path.read_text(encoding="utf-8")
    new_text = patched_text(text)
    if new_text is None:
        return 0
    if apply:
        path.write_text(new_text, encoding="utf-8")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="actually write changes")
    parser.add_argument("--root", default=str(DEFAULT_ROOT))
    args = parser.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        print(f"Root not found: {root}", file=sys.stderr)
        return 1

    changed = 0
    scanned = 0
    errors = 0
    for html in root.rglob("index.html"):
        scanned += 1
        try:
            changed += patch_file(html, args.apply)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            print(f"  ERROR {html}: {exc}", file=sys.stderr)
        if scanned % 5000 == 0:
            print(f"  ... scanned={scanned} candidates={changed} errors={errors}", flush=True)

    mode = "APPLIED" if args.apply else "DRY-RUN"
    print(f"[{mode}] scanned={scanned} candidates={changed} errors={errors}")
    if not args.apply and changed:
        print("No files changed. Re-run with --apply after reviewing the count.")
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
