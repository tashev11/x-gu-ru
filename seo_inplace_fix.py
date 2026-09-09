#!/usr/bin/env python3
"""Repair city grammar in already-deployed HTML.

Safe by default: the command only reports planned replacements. Real writes
require ``--apply``.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

ROOT = Path("/var/www/x-gu.ru/current")
CITIES_CSV = Path("/opt/p3-app/data/ru_cities_with_population.csv")


def city_prepositional(city_name: str) -> str:
    irregular = {
        "Москва": "Москве",
        "Санкт-Петербург": "Санкт-Петербурге",
        "Нижний Новгород": "Нижнем Новгороде",
        "Великий Новгород": "Великом Новгороде",
        "Орёл": "Орле",
        "Орел": "Орле",
        "Йошкар-Ола": "Йошкар-Оле",
        "Набережные Челны": "Набережных Челнах",
        "Минеральные Воды": "Минеральных Водах",
        "Великие Луки": "Великих Луках",
        "Ярославль": "Ярославле",
        "Севастополь": "Севастополе",
        "Ставрополь": "Ставрополе",
    }
    if city_name in irregular:
        return irregular[city_name]
    for sep in ("-на-", "-над-", "-под-"):
        if sep in city_name:
            head, _, tail = city_name.partition(sep)
            return city_prepositional(head) + sep + tail
    if city_name.endswith("ия"):
        return city_name[:-2] + "ии"
    if city_name.endswith("ый"):
        return city_name[:-2] + "ом"
    if city_name.endswith("ий"):
        return city_name[:-2] + "ем"
    if city_name.endswith("ой"):
        return city_name[:-2] + "ом"
    if city_name.endswith("а"):
        return city_name[:-1] + "е"
    if city_name.endswith("я"):
        return city_name[:-1] + "е"
    if city_name.endswith("ь"):
        return city_name[:-1] + "и"
    if city_name.endswith("ы"):
        return city_name[:-1] + "ах"
    if city_name.endswith(("о", "е", "и", "у", "ю", "э", "ё")):
        return city_name
    return city_name + "е"


def load_cities(pop_min: int = 0) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    with CITIES_CSV.open(encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            try:
                pop = int((row.get("population") or "0").replace(" ", "").replace("\xa0", ""))
            except Exception:
                pop = 0
            if pop < pop_min:
                continue
            slug = (row.get("slug") or "").strip()
            city = (row.get("city") or "").strip()
            if not slug or not city:
                continue
            rows.append((slug, city, city_prepositional(city)))
    return rows


def build_patterns(city: str, pp: str) -> list[tuple[re.Pattern, str]]:
    cyr = "[А-Яа-яЁё]"
    patterns: list[tuple[re.Pattern, str]] = []
    if city != pp:
        patterns.append((re.compile(rf"(\bв ){re.escape(city)}(?!{cyr})"), rf"\1{pp}"))
        patterns.append((re.compile(rf"(\b)по {re.escape(city)}(?!{cyr})"), rf"\1в {pp}"))
        patterns.append((re.compile(rf"(в <[a-z][^>]*>){re.escape(city)}(</[a-z]+>)"), rf"\1{pp}\2"))
        patterns.append((re.compile(rf"Да\. Для {re.escape(city)} поисковик"), "Да. Поисковик"))
    return patterns


def global_patterns() -> list[tuple[re.Pattern, str]]:
    return [
        (re.compile(r" и региону "), " и регионе "),
        (re.compile(r"Работаем по "), "Работаем в "),
    ]


def patch_file(path: Path, patterns: list[tuple[re.Pattern, str]], apply: bool) -> int:
    text = path.read_text(encoding="utf-8", errors="strict")
    total = 0
    new_text = text
    for pattern, replacement in patterns:
        new_text, count = pattern.subn(replacement, new_text)
        total += count
    if total > 0 and apply:
        path.write_text(new_text, encoding="utf-8")
    return total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="actually write changes")
    parser.add_argument("--only-slug", default=None, help="patch only this city slug")
    parser.add_argument("--pop-min", type=int, default=0)
    parser.add_argument("--root", default=str(ROOT))
    args = parser.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        print(f"Root not found: {root}", file=sys.stderr)
        return 1
    if not CITIES_CSV.is_file():
        print(f"Cities CSV not found: {CITIES_CSV}", file=sys.stderr)
        return 1

    cities = load_cities(args.pop_min)
    print(f"Loaded {len(cities)} cities (pop_min={args.pop_min})")
    if args.only_slug:
        cities = [city for city in cities if city[0] == args.only_slug]
        print(f"Filtered to slug={args.only_slug}: {len(cities)} cities")

    files_touched = 0
    files_scanned = 0
    total_repl = 0
    globals_ = global_patterns()

    for slug, city, pp in cities:
        city_dir = root / slug
        if not city_dir.is_dir():
            continue
        patterns = build_patterns(city, pp) + globals_
        for html in city_dir.rglob("index.html"):
            files_scanned += 1
            count = patch_file(html, patterns, args.apply)
            if count:
                files_touched += 1
                total_repl += count
        print(
            f"  {slug:<22} ({city} → {pp}): "
            f"files={sum(1 for _ in city_dir.rglob('index.html'))} "
            f"replacements_so_far={total_repl}"
        )

    mode = "APPLIED" if args.apply else "DRY-RUN"
    print(
        f"\n[{mode}] files_scanned={files_scanned} "
        f"files_touched={files_touched} replacements={total_repl}"
    )
    if not args.apply and total_repl:
        print("No files changed. Re-run with --apply after reviewing the plan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
