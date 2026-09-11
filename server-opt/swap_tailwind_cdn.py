#!/usr/bin/env python3
"""Replace Tailwind Play CDN with the local compiled stylesheet.

Idempotent and dry-run by default. Apply must target an isolated release
candidate unless an explicit emergency override allows active current.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from release_safety import (  # noqa: E402
    DEFAULT_CURRENT,
    DEFAULT_RELEASES_ROOT,
    atomic_replace_text,
    mutation_target_error,
)


DEFAULT_ROOT = DEFAULT_CURRENT
OLD = '<script src="https://cdn.tailwindcss.com"></script>'
NEW = '<link rel="stylesheet" href="/assets/tailwind.min.css">'


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

    if not args.root.is_dir():
        print(f"Root not found: {args.root}", file=sys.stderr)
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

    scanned = candidates = already = errors = 0
    for html in args.root.rglob("index.html"):
        scanned += 1
        try:
            text = html.read_text(encoding="utf-8", errors="strict")
            if OLD in text:
                candidates += 1
                if args.apply:
                    atomic_replace_text(html, text.replace(OLD, NEW))
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
    print(f"[{mode}] scanned={scanned} candidates={candidates} already={already} errors={errors}")
    if not args.apply and candidates:
        print("No files changed. Re-run against an isolated release candidate with --apply after review.")
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
