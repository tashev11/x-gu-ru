#!/usr/bin/env python3
"""Remove synthetic proof/review markup from generated HTML.

Safe by default: without ``--apply`` only candidate files are counted. Apply is
intended for an isolated release candidate; active-current writes need an
explicit emergency override. Each changed HTML file is replaced atomically.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


APP_ROOT = Path("/opt/p3-app")
DEFAULT_ROOT = Path("/var/www/x-gu.ru/current")

sys.path.insert(0, str(APP_ROOT))
os.chdir(APP_ROOT)

from content_generator import _sanitize_generated_html  # noqa: E402
from release_safety import (  # noqa: E402
    DEFAULT_CURRENT,
    DEFAULT_RELEASES_ROOT,
    atomic_replace_text,
    mutation_target_error,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="actually write sanitized HTML")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--current", type=Path, default=DEFAULT_CURRENT)
    parser.add_argument("--releases-root", type=Path, default=DEFAULT_RELEASES_ROOT)
    parser.add_argument(
        "--unsafe-allow-active-current",
        action="store_true",
        help="emergency override allowing writes directly to active current",
    )
    parser.add_argument("--limit", type=int, default=0, help="optional maximum files to scan")
    args = parser.parse_args()

    if not args.root.is_dir():
        print(f"Root not found: {args.root}", file=sys.stderr)
        return 1
    if args.limit < 0:
        print("--limit must be >= 0", file=sys.stderr)
        return 1

    if args.apply:
        target_error = mutation_target_error(
            args.root,
            current=args.current,
            releases_root=args.releases_root,
            allow_active_current=args.unsafe_allow_active_current,
        )
        if target_error:
            print(f"Refusing apply before scan/write: {target_error}", file=sys.stderr)
            return 3

    scanned = candidates = changed = errors = 0
    for html in args.root.rglob("index.html"):
        if args.limit and scanned >= args.limit:
            break
        scanned += 1
        try:
            text = html.read_text(encoding="utf-8", errors="strict")
            clean = _sanitize_generated_html(text)
            if clean == text:
                continue
            candidates += 1
            if args.apply:
                atomic_replace_text(html, clean)
                changed += 1
        except Exception as exc:  # noqa: BLE001
            errors += 1
            print(f"  ERROR {html}: {exc}", file=sys.stderr)

        if scanned % 5000 == 0:
            print(
                f"  ... scanned={scanned} candidates={candidates} "
                f"changed={changed} errors={errors}",
                flush=True,
            )

    mode = "APPLIED" if args.apply else "DRY-RUN"
    print(
        f"[{mode}] scanned={scanned} candidates={candidates} "
        f"changed={changed} errors={errors}"
    )

    if errors:
        print("Refusing successful completion because scan/write errors occurred.", file=sys.stderr)
        return 2

    if not args.apply and candidates:
        print("No files changed. Re-run against an isolated release candidate with --apply after review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
