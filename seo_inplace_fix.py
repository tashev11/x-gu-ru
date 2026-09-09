#!/usr/bin/env python3
"""In-place SEO fix: rewrite nominative city forms to prepositional case
in already-deployed HTML files under /var/www/x-gu.ru/current.

Safe regex strategy: only touch occurrences preceded by ' в ' or ' по '
(word boundary on the right). This preserves JSON-LD addressLocality,
breadcrumb labels, URLs, and other nominative uses.
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
    """Returns [(slug, city_name, prepositional)] for cities where forms differ."""
    rows: list[tuple[str, str, str]] = []
    with CITIES_CSV.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
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
            pp = city_prepositional(city)
            rows.append((slug, city, pp))
    return rows


def build_patterns(city: str, pp: str) -> list[tuple[re.Pattern, str]]:
    """Return list of (compiled_regex, replacement) for one city.

    Both ' в Cityname' (preposition, should be cityname_pp) and ' по Cityname'
    (dative misuse, reword to ' в Cityname_pp') are normalized to the
    prepositional form so the meta tags read naturally.

    The right-hand boundary uses Cyrillic-aware negative lookahead so we
    don't break inside longer words.
    """
    cyr = "[А-Яа-яЁё]"
    patterns: list[tuple[re.Pattern, str]] = []
    if city != pp:
        # " в Cityname" not followed by a Cyrillic letter (word-boundary safe)
        patterns.append((re.compile(rf"(\bв ){re.escape(city)}(?!{cyr})"), rf"\1{pp}"))
        # " по Cityname" → " в Cityname_pp"
        patterns.append((re.compile(rf"(\b)по {re.escape(city)}(?!{cyr})"), rf"\1в {pp}"))
        # "в <span ...>Cityname</span>" — H1/heading where city is wrapped in element
        patterns.append((re.compile(rf"(в <[a-z][^>]*>){re.escape(city)}(</[a-z]+>)"), rf"\1{pp}\2"))
        # FAQ stock phrase "Да. Для Cityname поисковик" → drop the "Для Cityname " prefix
        patterns.append((re.compile(rf"Да\. Для {re.escape(city)} поисковик"), "Да. Поисковик"))
    return patterns


def global_patterns() -> list[tuple[re.Pattern, str]]:
    """Patterns independent of city — applied to every file once."""
    return [
        # "и региону X" left over from old "по {city_pp} и региону" template; "в Абакане и региону" is awkward
        (re.compile(r" и региону "), " и регионе "),
        # Unify "Работаем по Cityname" → "Работаем в Cityname" so the sentence reads as prep+prep
        # consistently with "и регионе Х" that follows. Touches both correctly-cased cities
        # (Москве, Уфе, Казани) and any leftover dative/prepositional mix.
        (re.compile(r"Работаем по "), "Работаем в "),
    ]


def patch_file(path: Path, patterns: list[tuple[re.Pattern, str]], dry_run: bool) -> int:
    """Apply patterns to file. Return number of replacements made."""
    text = path.read_text(encoding="utf-8", errors="strict")
    total = 0
    new_text = text
    for pat, repl in patterns:
        new_text, n = pat.subn(repl, new_text)
        total += n
    if total > 0 and not dry_run:
        path.write_text(new_text, encoding="utf-8")
    return total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only-slug", default=None, help="Patch only this city slug (testing)")
    parser.add_argument("--pop-min", type=int, default=0)
    parser.add_argument("--root", default=str(ROOT))
    args = parser.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        print(f"Root not found: {root}", file=sys.stderr)
        return 1

    cities = load_cities(args.pop_min)
    print(f"Loaded {len(cities)} cities (pop_min={args.pop_min})")
    if args.only_slug:
        cities = [c for c in cities if c[0] == args.only_slug]
        print(f"Filtered to slug={args.only_slug}: {len(cities)} cities")

    files_touched = 0
    files_scanned = 0
    total_repl = 0
    g_patterns = global_patterns()

    for slug, city, pp in cities:
        city_dir = root / slug
        if not city_dir.is_dir():
            continue
        per_city = build_patterns(city, pp)
        all_patterns = per_city + g_patterns
        if not all_patterns:
            continue
        for html in city_dir.rglob("index.html"):
            files_scanned += 1
            n = patch_file(html, all_patterns, args.dry_run)
            if n > 0:
                files_touched += 1
                total_repl += n
        print(f"  {slug:<22} ({city} → {pp}): files={sum(1 for _ in city_dir.rglob('index.html'))} replacements_so_far={total_repl}")

    mode = "DRY-RUN" if args.dry_run else "APPLIED"
    print(f"\n[{mode}] files_scanned={files_scanned} files_touched={files_touched} replacements={total_repl}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
