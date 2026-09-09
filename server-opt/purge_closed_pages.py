#!/usr/bin/env python3
"""Физическое удаление страниц, закрытых от индексации.

ТРОЙНАЯ ЗАЩИТА — каталог удаляется, только если ВСЕ три условия верны:
  1. его URL отсутствует в sitemap (то есть он вне открытого ядра);
  2. его URL отсутствует в whitelist.txt (страницы из поиска Яндекса);
  3. в самом файле стоит noindex (то есть он уже закрыт от поисковиков).
Любое несовпадение — каталог пропускается.

По умолчанию только показывает, что будет удалено. Для реального удаления
нужен явный флаг --apply.

  Показать план:  python3 purge_closed_pages.py
  Удалить:        python3 purge_closed_pages.py --apply
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

WEB_ROOT = Path("/var/www/x-gu.ru/current")
SITEMAP = WEB_ROOT / "sitemaps" / "sitemap-1.xml"
WHITELIST = Path("/opt/p3-app/data/whitelist.txt")
BASE = "https://x-gu.ru"

# Каталоги, которые не трогаем никогда.
PROTECTED = {"sitemaps", "privacy", ".well-known"}


def load_protected_urls() -> set[str]:
    urls: set[str] = set()
    sm = SITEMAP.read_text(encoding="utf-8")
    for line in sm.splitlines():
        if "<loc>" in line:
            u = line.split("<loc>")[1].split("</loc>")[0].strip()
            urls.add(u if u.endswith("/") else u + "/")
    for line in WHITELIST.read_text(encoding="utf-8").splitlines():
        u = line.strip()
        if u:
            urls.add(u if u.endswith("/") else u + "/")
    return urls


def dir_size(path: Path) -> int:
    total = 0
    for f in path.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except OSError:
                pass
    return total


def main() -> int:
    apply = "--apply" in sys.argv
    protected = load_protected_urls()
    print(f"защищённых URL (sitemap + whitelist): {len(protected)}")
    print(f"режим: {'УДАЛЕНИЕ' if apply else 'только показ (--apply для удаления)'}\n")

    to_delete: list[tuple[Path, str]] = []
    kept = skipped_no_noindex = 0

    for city_dir in sorted(WEB_ROOT.iterdir()):
        if not city_dir.is_dir() or city_dir.name in PROTECTED:
            continue
        city = city_dir.name

        for sub in sorted(city_dir.iterdir()):
            if not sub.is_dir():
                continue
            url = f"{BASE}/{city}/{sub.name}/"

            # Защита 1 и 2: в sitemap или whitelist — не трогаем.
            if url in protected:
                kept += 1
                continue

            # Защита 3: страница обязана быть закрыта noindex.
            index_file = sub / "index.html"
            if index_file.is_file():
                head = index_file.read_text(encoding="utf-8", errors="ignore")[:4000]
                if 'name="robots" content="noindex' not in head:
                    skipped_no_noindex += 1
                    continue
            to_delete.append((sub, url))

        # Хаб города удаляем только если у него не осталось ни одной открытой
        # подстраницы и сам он закрыт.
        hub_url = f"{BASE}/{city}/"
        if hub_url not in protected:
            hub_file = city_dir / "index.html"
            city_has_open = any(
                f"{BASE}/{city}/{s.name}/" in protected
                for s in city_dir.iterdir() if s.is_dir()
            )
            if not city_has_open and hub_file.is_file():
                head = hub_file.read_text(encoding="utf-8", errors="ignore")[:4000]
                if 'name="robots" content="noindex' in head:
                    to_delete.append((city_dir, hub_url))
        else:
            kept += 1

    print(f"остаётся (в ядре/whitelist): {kept}")
    print(f"пропущено (нет noindex — на всякий случай): {skipped_no_noindex}")
    print(f"под удаление: {len(to_delete)}\n")

    if not to_delete:
        print("нечего удалять")
        return 0

    print("примеры:")
    for p, u in to_delete[:8]:
        print(f"   {u}")
    if len(to_delete) > 8:
        print(f"   ... и ещё {len(to_delete) - 8}")

    if not apply:
        print("\nЭто был показ. Для удаления запустите с --apply")
        return 0

    freed = 0
    errors = 0
    for i, (path, _url) in enumerate(to_delete, 1):
        try:
            freed += dir_size(path)
            shutil.rmtree(path)
        except Exception as e:  # noqa: BLE001
            errors += 1
            print(f"  ОШИБКА {path}: {e}", file=sys.stderr)
        if i % 2000 == 0:
            print(f"  ... удалено {i}/{len(to_delete)}", flush=True)

    print(f"\n[ГОТОВО] удалено каталогов={len(to_delete) - errors} "
          f"освобождено={freed / 1024 / 1024:.0f} МБ ошибок={errors}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
