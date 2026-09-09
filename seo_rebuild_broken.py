#!/usr/bin/env python3
"""Rebuild pages for known broken city directories.

Safe by default: without ``--apply`` this command only prints the rebuild plan.
Real writes and chmod operations require an explicit flag. Rendering is routed
through the hardened top-level ``content_generator`` facade.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, "/opt/p3-app")
os.chdir("/opt/p3-app")

from content_generator import (  # noqa: E402
    _render_city_hub_html,
    _render_html_landing,
)

PUBLIC_ROOT = Path("/var/www/x-gu.ru/current")
KEYWORDS_CSV = Path("/opt/p3-app/data/keywords_all.csv")
CITIES_CSV = Path("/opt/p3-app/data/ru_cities_with_population.csv")

BROKEN_CITY_SLUGS = {
    "tula",
    "tver",
    "ussuriisk",
    "velikii-novgorod",
    "vladimir",
    "volgodonsk",
    "vologda",
    "voronezh",
    "tiumen",
    "tobolsk",
}


def load_city(slug: str) -> SimpleNamespace | None:
    with CITIES_CSV.open(encoding="utf-8") as file:
        for row in csv.DictReader(file):
            if (row.get("slug") or "").strip() == slug:
                return SimpleNamespace(
                    slug=slug,
                    name=(row.get("city") or "").strip(),
                    region=(row.get("region") or "Россия").strip(),
                )
    return None


def load_services() -> list[SimpleNamespace]:
    services: list[SimpleNamespace] = []
    with KEYWORDS_CSV.open(encoding="utf-8") as file:
        for row in csv.DictReader(file):
            name = (row.get("name") or "").strip()
            slug = (row.get("slug") or "").strip()
            niche = (row.get("niche") or "SEO").strip()
            if name and slug:
                services.append(SimpleNamespace(name=name, slug=slug, niche=niche))
    return services


def chmod_recursive(path: Path, dir_mode: int = 0o755, file_mode: int = 0o644) -> None:
    try:
        os.chmod(path, dir_mode if path.is_dir() else file_mode)
    except OSError as exc:
        print(f"  chmod fail {path}: {exc}", file=sys.stderr)
    if path.is_dir():
        for child in path.iterdir():
            chmod_recursive(child, dir_mode, file_mode)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="actually rebuild files")
    parser.add_argument("--root", type=Path, default=PUBLIC_ROOT)
    parser.add_argument(
        "--slug",
        action="append",
        default=[],
        help="limit to a city slug; may be supplied multiple times",
    )
    args = parser.parse_args()

    if not CITIES_CSV.is_file() or not KEYWORDS_CSV.is_file():
        print("Required city/keyword CSV data is missing", file=sys.stderr)
        return 1

    services = load_services()
    requested = set(args.slug) if args.slug else set(BROKEN_CITY_SLUGS)
    unknown = requested - BROKEN_CITY_SLUGS
    if unknown:
        print(
            "Refusing slugs outside the reviewed BROKEN_CITY_SLUGS set: "
            + ", ".join(sorted(unknown)),
            file=sys.stderr,
        )
        return 2

    plan: list[tuple[str, SimpleNamespace]] = []
    for slug in sorted(requested):
        city = load_city(slug)
        if city is None:
            print(f"  SKIP {slug}: not found in CSV")
            continue
        plan.append((slug, city))

    pages_per_city = 1 + len(services)
    print(
        f"plan: cities={len(plan)} services={len(services)} "
        f"pages_per_city={pages_per_city} total_pages={len(plan) * pages_per_city}"
    )
    for slug, city in plan:
        print(f"  {slug}: {city.name} -> {args.root / slug}")

    if not args.apply:
        print("[DRY-RUN] No files changed. Re-run with --apply after reviewing the plan.")
        return 0

    site = SimpleNamespace(id=1)
    total_built = 0
    for slug, city in plan:
        city_dir = args.root / slug
        city_dir.mkdir(parents=True, exist_ok=True)

        hub_html = _render_city_hub_html(city, services)
        (city_dir / "index.html").write_text(hub_html, encoding="utf-8")
        built = 1

        for service in services:
            service_dir = city_dir / service.slug
            service_dir.mkdir(parents=True, exist_ok=True)
            html = _render_html_landing(site, city, service)
            (service_dir / "index.html").write_text(html, encoding="utf-8")
            built += 1

        chmod_recursive(city_dir)
        total_built += built
        print(f"  {slug}: built {built} pages")

    print(f"[APPLIED] total_pages_built={total_built}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
