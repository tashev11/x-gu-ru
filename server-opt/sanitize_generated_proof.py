#!/usr/bin/env python3
"""Remove synthetic proof/review markup from already-deployed HTML.

The hardened generator sanitizes all newly rendered pages. This migration
brings existing deployed HTML to the same state without requiring a full site
rebuild.

Safe by default: without ``--apply`` only counts candidate files. Real writes
require an explicit flag.
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="actually write sanitized HTML")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--limit", type=int, default=0, help="optional maximum files to scan")
    args = parser.parse_args()

    if not args.root.is_dir():
        print(f"Root not found: {args.root}", file=sys.stderr)
        return 1

    scanned = candidates = changed = errors = 0
    for html in args.root.rglob("index.html"):
        if args.limit and scanned >= args.limit:
            break
        scanned += 1
        try:
            text = html.read_text(encoding="utf-8")
            clean = _sanitize_generated_html(text)
            if clean == text:
                continue
            candidates += 1
            if args.apply:
                html.write_text(clean, encoding="utf-8")
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
    if not args.apply and candidates:
        print("No files changed. Re-run with --apply after reviewing the count.")
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
