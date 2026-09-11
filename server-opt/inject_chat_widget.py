#!/usr/bin/env python3
"""Inject the Elka chat widget into generated HTML.

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
SNIPPET = (
    "    <!-- Виджет чата Эльки -->\n"
    '    <script src="https://app.l-ka.ru/widget/loader.js" '
    'data-site="st_e8b5e472b977bab7" async></script>\n'
)
MARKER = "app.l-ka.ru/widget/loader.js"


def inject(text: str) -> str | None:
    if MARKER in text:
        return None
    low = text.lower()
    index = low.rfind("</body>")
    if index == -1:
        index = low.rfind("</html>")
    if index == -1:
        return text + "\n" + SNIPPET
    return text[:index] + SNIPPET + text[index:]


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
            new = inject(text)
            if new is None:
                already += 1
            else:
                candidates += 1
                if args.apply:
                    atomic_replace_text(html, new)
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
