#!/usr/bin/env python3
"""Inject the Elka chat widget into every deployed index.html.

The site has no shared layout (each page is self-contained), so the snippet
must be added to every file. Idempotent: skips files that already contain the
loader URL. Inserts before the last </body> (fallback </html>, else append).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path("/var/www/x-gu.ru/current")

SNIPPET = (
    "    <!-- Виджет чата Эльки -->\n"
    '    <script src="https://app.l-ka.ru/widget/loader.js" '
    'data-site="st_e8b5e472b977bab7" async></script>\n'
)
MARKER = "app.l-ka.ru/widget/loader.js"


def inject(text: str) -> str | None:
    """Return new text with snippet inserted, or None if no change needed."""
    if MARKER in text:
        return None  # already present
    low = text.lower()
    idx = low.rfind("</body>")
    if idx == -1:
        idx = low.rfind("</html>")
    if idx == -1:
        return text + "\n" + SNIPPET  # no closing tags: append
    return text[:idx] + SNIPPET + text[idx:]


def main() -> int:
    if not ROOT.is_dir():
        print(f"Root not found: {ROOT}", file=sys.stderr)
        return 1
    scanned = injected = already = errors = 0
    for html in ROOT.rglob("index.html"):
        scanned += 1
        try:
            text = html.read_text(encoding="utf-8")
            new = inject(text)
            if new is None:
                already += 1
            else:
                html.write_text(new, encoding="utf-8")
                injected += 1
        except Exception as e:  # noqa: BLE001
            errors += 1
            print(f"  ERROR {html}: {e}", file=sys.stderr)
        if scanned % 5000 == 0:
            print(f"  ... scanned={scanned} injected={injected} "
                  f"already={already} errors={errors}", flush=True)
    print(f"[DONE] scanned={scanned} injected={injected} "
          f"already={already} errors={errors}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
