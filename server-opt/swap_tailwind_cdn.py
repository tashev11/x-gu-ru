#!/usr/bin/env python3
"""Замена Tailwind Play CDN на собранный локальный CSS.

Play CDN грузит ~400 КБ JavaScript и компилирует стили прямо в браузере
пользователя. Собранный файл — 37 КБ (7 КБ после сжатия) и кэшируется
на 30 дней.

Идемпотентно: повторный запуск ничего не меняет.
"""
from __future__ import annotations

import sys
from pathlib import Path

WEB_ROOT = Path("/var/www/x-gu.ru/current")
OLD = '<script src="https://cdn.tailwindcss.com"></script>'
NEW = '<link rel="stylesheet" href="/assets/tailwind.min.css">'


def main() -> int:
    scanned = changed = already = errors = 0
    for html in WEB_ROOT.rglob("index.html"):
        scanned += 1
        try:
            text = html.read_text(encoding="utf-8")
            if OLD in text:
                html.write_text(text.replace(OLD, NEW), encoding="utf-8")
                changed += 1
            elif NEW in text:
                already += 1
        except Exception as e:  # noqa: BLE001
            errors += 1
            print(f"  ОШИБКА {html}: {e}", file=sys.stderr)
        if scanned % 500 == 0:
            print(f"  ... {scanned} просмотрено, {changed} заменено", flush=True)

    print(f"[ГОТОВО] просмотрено={scanned} заменено={changed} "
          f"уже было={already} ошибок={errors}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
