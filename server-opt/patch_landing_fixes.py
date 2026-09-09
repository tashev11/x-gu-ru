#!/usr/bin/env python3
"""In-place fixes for deployed landing pages:
  - website input type=url -> text (url type blocked submit without scheme)
  - footer dead links -> on-page anchors / /privacy/ ; remove non-existent
  - form privacy link -> /privacy/
  - Telegram @avitobibot -> @tashev116

All replacements are exact static strings (idempotent: re-running is a no-op).
Runs over every index.html; landing-specific strings are no-ops on hubs/home.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path("/var/www/x-gu.ru/current")

FOOT = 'class="hover:text-blue-400 transition"'
REPLACEMENTS = [
    # website field
    ('type="url" id="website"', 'type="text" inputmode="url" id="website"'),
    # footer "Услуги"
    (f'<a href="#" {FOOT}>Создание сайта</a>', f'<a href="#services" {FOOT}>Создание сайта</a>'),
    (f'<a href="#" {FOOT}>SEO Оптимизация</a>', f'<a href="#services" {FOOT}>SEO Оптимизация</a>'),
    (f'<a href="#" {FOOT}>Контекстная реклама</a>', f'<a href="#services" {FOOT}>Контекстная реклама</a>'),
    (f'<a href="#" {FOOT}>Автоматизация</a>', f'<a href="#services" {FOOT}>Автоматизация</a>'),
    (f'<a href="#" {FOOT}>SEO Аудит</a>', f'<a href="#audit" {FOOT}>SEO Аудит</a>'),
    # footer "Компания"
    (f'<a href="#" {FOOT}>О нас</a>', f'<a href="#founder" {FOOT}>О нас</a>'),
    (f'<a href="#" {FOOT}>Кейсы</a>', f'<a href="#reviews" {FOOT}>Кейсы</a>'),
    (f'<a href="#" {FOOT}>Отзывы</a>', f'<a href="#reviews" {FOOT}>Отзывы</a>'),
    (f'<li><a href="#" {FOOT}>Блог</a></li>', ''),  # remove (no page)
    (f'<a href="#" {FOOT}>Контакты</a>', f'<a href="#contact" {FOOT}>Контакты</a>'),
    # bottom legal
    (f'<a href="#" {FOOT}>Политика конфиденциальности</a>', f'<a href="/privacy/" {FOOT}>Политика конфиденциальности</a>'),
    (f'<a href="#" {FOOT}>Договор оферты</a>', ''),  # remove (no page)
    # form consent link
    ('<a href="#" class="text-blue-600 hover:underline">политикой конфиденциальности</a>',
     '<a href="/privacy/" class="text-blue-600 hover:underline">политикой конфиденциальности</a>'),
    # telegram handle (covers t.me/avitobibot and @avitobibot)
    ('avitobibot', 'tashev116'),
]


def patch(text: str) -> tuple[str, int]:
    n = 0
    for old, new in REPLACEMENTS:
        if old in text:
            cnt = text.count(old)
            text = text.replace(old, new)
            n += cnt
    return text, n


def main() -> int:
    if not ROOT.is_dir():
        print(f"Root not found: {ROOT}", file=sys.stderr)
        return 1
    scanned = touched = total = errors = 0
    for html in ROOT.rglob("index.html"):
        scanned += 1
        try:
            text = html.read_text(encoding="utf-8")
            new, n = patch(text)
            if n:
                html.write_text(new, encoding="utf-8")
                touched += 1
                total += n
        except Exception as e:  # noqa: BLE001
            errors += 1
            print(f"  ERROR {html}: {e}", file=sys.stderr)
        if scanned % 5000 == 0:
            print(f"  ... scanned={scanned} touched={touched} repl={total} errors={errors}", flush=True)
    print(f"[DONE] scanned={scanned} touched={touched} repl={total} errors={errors}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
