#!/usr/bin/env python3
"""Repair city grammar in generated HTML.

Dry-run by default. Apply must target an isolated release candidate unless an
explicit emergency override allows active current. Writes are atomic per file.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

from city_morphology import city_prepositional
from release_safety import (
    DEFAULT_CURRENT,
    DEFAULT_RELEASES_ROOT,
    atomic_replace_text,
    mutation_target_error,
)


ROOT = DEFAULT_CURRENT
CITIES_CSV = Path("/opt/p3-app/data/ru_cities_with_population.csv")


def load_cities(pop_min: int = 0) -> list[tuple[str, str, str]]:
    rows: list[tuple[str, str, str]] = []
    with CITIES_CSV.open(encoding="utf-8", errors="strict") as file:
        reader = csv.DictReader(file)
        for row in reader:
            try:
                pop = int((row.get("population") or "0").replace(" ", "").replace("\xa0", ""))
            except ValueError:
                pop = 0
            if pop < pop_min:
                continue
            slug = (row.get("slug") or "").strip()
            city = (row.get("city") or "").strip()
            if not slug or not city:
                continue
            rows.append((slug, city, city_prepositional(city)))
    return rows


def build_patterns(city: str, pp: str) -> list[tuple[re.Pattern[str], str]]:
    cyr = "[А-Яа-яЁё]"
    patterns: list[tuple[re.Pattern[str], str]] = []
    if city != pp:
        patterns.append((re.compile(rf"(\bв ){re.escape(city)}(?!{cyr})"), rf"\1{pp}"))
        patterns.append((re.compile(rf"(\b)по {re.escape(city)}(?!{cyr})"), rf"\1в {pp}"))
        patterns.append((re.compile(rf"(в <[a-z][^>]*>){re.escape(city)}(</[a-z]+>)"), rf"\1{pp}\2"))
        patterns.append((re.compile(rf"Да\. Для {re.escape(city)} поисковик"), "Да. Поисковик"))
    return patterns


def global_patterns() -> list[tuple[re.Pattern[str], str]]:
    return [
        (re.compile(r" и региону "), " и регионе "),
        (re.compile(r"Работаем по "), "Работаем в "),
    ]


def patch_file(path: Path, patterns: list[tuple[re.Pattern[str], str]], apply: bool) -> int:
    text = path.read_text(encoding="utf-8", errors="strict")
    total = 0
    new_text = text
    for pattern, replacement in patterns:
        new_text, count = pattern.subn(replacement, new_text)
        total += count
    if total > 0 and apply:
        atomic_replace_text(path, new_text)
    return total


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="actually write changes")
    parser.add_argument("--only-slug", default=None, help="patch only this city slug")
    parser.add_argument("--pop-min", type=int, default=0)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--current", type=Path, default=DEFAULT_CURRENT)
    parser.add_argument("--releases-root", type=Path, default=DEFAULT_RELEASES_ROOT)
    parser.add_argument(
        "--unsafe-allow-active-current",
        action="store_true",
        help="emergency override allowing writes directly to active current",
    )
    args = parser.parse_args()

    root = args.root
    if not root.is_dir():
        print(f"Root not found: {root}", file=sys.stderr)
        return 1
    if not CITIES_CSV.is_file():
        print(f"Cities CSV not found: {CITIES_CSV}", file=sys.stderr)
        return 1
    if args.pop_min < 0:
        print("--pop-min must be >= 0", file=sys.stderr)
        return 1

    if args.apply:
        target_error = mutation_target_error(
            root,
            current=args.current,
            releases_root=args.releases_root,
            allow_active_current=args.unsafe_allow_active_current,
        )
        if target_error:
            print(f"Refusing apply before scan/write: {target_error}", file=sys.stderr)
            return 3

    cities = load_cities(args.pop_min)
    print(f"Loaded {len(cities)} cities (pop_min={args.pop_min})")
    if args.only_slug:
        cities = [city for city in cities if city[0] == args.only_slug]
        print(f"Filtered to slug={args.only_slug}: {len(cities)} cities")
        if not cities:
            print(f"Requested city slug not found in CSV: {args.only_slug}", file=sys.stderr)
            return 2

    files_touched = 0
    files_scanned = 0
    total_repl = 0
    globals_ = global_patterns()

    for slug, city, pp in cities:
        city_dir = root / slug
        if not city_dir.is_dir():
            continue
        patterns = build_patterns(city, pp) + globals_
        city_files = list(city_dir.rglob("index.html"))
        for html in city_files:
            files_scanned += 1
            try:
                count = patch_file(html, patterns, args.apply)
            except Exception as exc:  # noqa: BLE001
                print(f"  ERROR {html}: {exc}", file=sys.stderr)
                return 4 if args.apply else 2
            if count:
                files_touched += 1
                total_repl += count
        print(
            f"  {slug:<22} ({city} → {pp}): "
            f"files={len(city_files)} replacements_so_far={total_repl}"
        )

    mode = "APPLIED" if args.apply else "DRY-RUN"
    print(
        f"\n[{mode}] files_scanned={files_scanned} "
        f"files_touched={files_touched} replacements={total_repl}"
    )
    if not args.apply and total_repl:
        print("No files changed. Re-run against an isolated release candidate with --apply after review.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
