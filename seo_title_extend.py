#!/usr/bin/env python3
"""Extend titles shorter than 30 chars by inserting `` и регионе``.

Dry-run by default. Apply must target an isolated release candidate unless an
explicit emergency override allows active current. Writes are atomic per file.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from release_safety import (
    DEFAULT_CURRENT,
    DEFAULT_RELEASES_ROOT,
    atomic_replace_text,
    mutation_target_error,
)


DEFAULT_ROOT = DEFAULT_CURRENT
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
    match = re_title.search(text)
    if not match:
        return None
    old_title = match.group(1).strip()
    if len(old_title) >= 30:
        return None
    new_title = extend_title(old_title)
    if new_title == old_title:
        return None

    text = text.replace(f"<title>{old_title}</title>", f"<title>{new_title}</title>", 1)

    def title_repl(meta_match: re.Match[str]) -> str:
        return meta_match.group(1) + extend_title(meta_match.group(2)) + meta_match.group(3)

    text = re_og_title.sub(title_repl, text, count=1)
    text = re_tw_title.sub(title_repl, text, count=1)
    text = re_jsonld_name.sub(title_repl, text, count=1)
    return text


def patch_file(path: Path, apply: bool) -> int:
    text = path.read_text(encoding="utf-8", errors="strict")
    new_text = patched_text(text)
    if new_text is None:
        return 0
    if apply:
        atomic_replace_text(path, new_text)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="actually write changes")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--current", type=Path, default=DEFAULT_CURRENT)
    parser.add_argument("--releases-root", type=Path, default=DEFAULT_RELEASES_ROOT)
    parser.add_argument(
        "--unsafe-allow-active-current",
        action="store_true",
        help="emergency override allowing writes directly to active current",
    )
    args = parser.parse_args()

    root = args.root
    if not root.is_dir():
        print(f"Root not found: {root}", file=sys.stderr)
        return 1

    if args.apply:
        target_error = mutation_target_error(
            root,
            current=args.current,
            releases_root=args.releases_root,
            allow_active_current=args.unsafe_allow_active_current,
        )
        if target_error:
            print(f"Refusing apply before scan/write: {target_error}", file=sys.stderr)
            return 3

    candidates = scanned = errors = 0
    for html in root.rglob("index.html"):
        scanned += 1
        try:
            candidates += patch_file(html, args.apply)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            print(f"  ERROR {html}: {exc}", file=sys.stderr)
            if args.apply:
                print("Apply stopped after first write error; discard/rebuild this release candidate.", file=sys.stderr)
                return 4
        if scanned % 5000 == 0:
            print(f"  ... scanned={scanned} candidates={candidates} errors={errors}", flush=True)

    mode = "APPLIED" if args.apply else "DRY-RUN"
    print(f"[{mode}] scanned={scanned} candidates={candidates} errors={errors}")
    if not args.apply and candidates:
        print("No files changed. Re-run against an isolated release candidate with --apply after review.")
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
