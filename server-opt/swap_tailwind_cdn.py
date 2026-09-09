#!/usr/bin/env python3
"""Replace Tailwind Play CDN with the local compiled stylesheet.

Idempotent and safe by default: use ``--apply`` to write changes.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_ROOT = Path("/var/www/x-gu.ru/current")
OLD = '<script src="https://cdn.tailwindcss.com"></script>'
NEW = '<link rel="stylesheet" href="/assets/tailwind.min.css">'


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="actually write changes")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()

    if not args.root.is_dir():
        print(f"Root not found: {args.root}", file=sys.stderr)
        return 1

    scanned = candidates = already = errors = 0
    for html in args.root.rglob("index.html"):
        scanned += 1
        try:
            text = html.read_text(encoding="utf-8")
            if OLD in text:
                candidates += 1
                if args.apply:
                    html.write_text(text.replace(OLD, NEW), encoding="utf-8")
            elif NEW in text:
                already += 1
        except Exception as exc:  # noqa: BLE001
            errors += 1
            print(f"  ERROR {html}: {exc}", file=sys.stderr)
        if scanned % 5000 == 0:
            print(
                f"  ... scanned={scanned} candidates={candidates} "
                f"already={already} errors={errors}",
                flush=True,
            )

    mode = "APPLIED" if args.apply else "DRY-RUN"
    print(
        f"[{mode}] scanned={scanned} candidates={candidates} "
        f"already={already} errors={errors}"
    )
    if not args.apply and candidates:
        print("No files changed. Re-run with --apply after reviewing the count.")
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
