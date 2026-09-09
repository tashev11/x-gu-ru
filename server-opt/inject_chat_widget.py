#!/usr/bin/env python3
"""Inject the Elka chat widget into deployed HTML.

Idempotent and safe by default: without ``--apply`` the command only reports
how many files would change.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_ROOT = Path("/var/www/x-gu.ru/current")

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
    args = parser.parse_args()

    if not args.root.is_dir():
        print(f"Root not found: {args.root}", file=sys.stderr)
        return 1

    scanned = candidates = already = errors = 0
    for html in args.root.rglob("index.html"):
        scanned += 1
        try:
            text = html.read_text(encoding="utf-8")
            new = inject(text)
            if new is None:
                already += 1
            else:
                candidates += 1
                if args.apply:
                    html.write_text(new, encoding="utf-8")
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
