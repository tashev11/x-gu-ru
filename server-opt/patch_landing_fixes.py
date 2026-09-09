#!/usr/bin/env python3
"""Apply known idempotent fixes to deployed landing pages.

Safe by default: the script reports planned replacements only. Use ``--apply``
to write files after reviewing the counts.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

DEFAULT_ROOT = Path("/var/www/x-gu.ru/current")
FOOT = 'class="hover:text-blue-400 transition"'
REPLACEMENTS = [
    ('type="url" id="website"', 'type="text" inputmode="url" id="website"'),
    (f'<a href="#" {FOOT}>Создание сайта</a>', f'<a href="#services" {FOOT}>Создание сайта</a>'),
    (f'<a href="#" {FOOT}>SEO Оптимизация</a>', f'<a href="#services" {FOOT}>SEO Оптимизация</a>'),
    (f'<a href="#" {FOOT}>Контекстная реклама</a>', f'<a href="#services" {FOOT}>Контекстная реклама</a>'),
    (f'<a href="#" {FOOT}>Автоматизация</a>', f'<a href="#services" {FOOT}>Автоматизация</a>'),
    (f'<a href="#" {FOOT}>SEO Аудит</a>', f'<a href="#audit" {FOOT}>SEO Аудит</a>'),
    (f'<a href="#" {FOOT}>О нас</a>', f'<a href="#founder" {FOOT}>О нас</a>'),
    (f'<a href="#" {FOOT}>Кейсы</a>', f'<a href="#reviews" {FOOT}>Кейсы</a>'),
    (f'<a href="#" {FOOT}>Отзывы</a>', f'<a href="#reviews" {FOOT}>Отзывы</a>'),
    (f'<li><a href="#" {FOOT}>Блог</a></li>', ''),
    (f'<a href="#" {FOOT}>Контакты</a>', f'<a href="#contact" {FOOT}>Контакты</a>'),
    (
        f'<a href="#" {FOOT}>Политика конфиденциальности</a>',
        f'<a href="/privacy/" {FOOT}>Политика конфиденциальности</a>',
    ),
    (f'<a href="#" {FOOT}>Договор оферты</a>', ''),
    (
        '<a href="#" class="text-blue-600 hover:underline">политикой конфиденциальности</a>',
        '<a href="/privacy/" class="text-blue-600 hover:underline">политикой конфиденциальности</a>',
    ),
    ('avitobibot', 'tashev116'),
]


def patch(text: str) -> tuple[str, int]:
    count = 0
    for old, new in REPLACEMENTS:
        if old in text:
            matches = text.count(old)
            text = text.replace(old, new)
            count += matches
    return text, count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="actually write changes")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()

    if not args.root.is_dir():
        print(f"Root not found: {args.root}", file=sys.stderr)
        return 1

    scanned = touched = total = errors = 0
    for html in args.root.rglob("index.html"):
        scanned += 1
        try:
            text = html.read_text(encoding="utf-8")
            new_text, replacements = patch(text)
            if replacements:
                touched += 1
                total += replacements
                if args.apply:
                    html.write_text(new_text, encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            errors += 1
            print(f"  ERROR {html}: {exc}", file=sys.stderr)
        if scanned % 5000 == 0:
            print(
                f"  ... scanned={scanned} touched={touched} "
                f"replacements={total} errors={errors}",
                flush=True,
            )

    mode = "APPLIED" if args.apply else "DRY-RUN"
    print(
        f"[{mode}] scanned={scanned} touched={touched} "
        f"replacements={total} errors={errors}"
    )
    if not args.apply and total:
        print("No files changed. Re-run with --apply after reviewing the plan.")
    return 0 if errors == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
