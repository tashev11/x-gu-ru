#!/usr/bin/env python3
"""Rebuild pages for a reviewed set of broken city directories.

Dry-run by default. Apply must target an isolated release candidate unless an
explicit emergency override allows active current. Missing city/service input
is a hard error before any directory or file is created.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path
from types import SimpleNamespace


APP_ROOT = Path("/opt/p3-app")
sys.path.insert(0, str(APP_ROOT))
os.chdir(APP_ROOT)

from content_generator import _render_city_hub_html, _render_html_landing  # noqa: E402
from release_safety import (  # noqa: E402
    DEFAULT_CURRENT,
    DEFAULT_RELEASES_ROOT,
    atomic_replace_text,
    mutation_target_error,
)


PUBLIC_ROOT = DEFAULT_CURRENT
KEYWORDS_CSV = APP_ROOT / "data/keywords_all.csv"
CITIES_CSV = APP_ROOT / "data/ru_cities_with_population.csv"

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
    with CITIES_CSV.open(encoding="utf-8", errors="strict") as file:
        for row in csv.DictReader(file):
            if (row.get("slug") or "").strip() == slug:
                name = (row.get("city") or "").strip()
                if not name:
                    return None
                return SimpleNamespace(
                    slug=slug,
                    name=name,
                    region=(row.get("region") or "Россия").strip(),
                )
    return None


def load_services() -> list[SimpleNamespace]:
    services: list[SimpleNamespace] = []
    with KEYWORDS_CSV.open(encoding="utf-8", errors="strict") as file:
        for row in csv.DictReader(file):
            name = (row.get("name") or "").strip()
            slug = (row.get("slug") or "").strip()
            niche = (row.get("niche") or "SEO").strip()
            if name and slug:
                services.append(SimpleNamespace(name=name, slug=slug, niche=niche))
    return services


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="actually rebuild files")
    parser.add_argument("--root", type=Path, default=PUBLIC_ROOT)
    parser.add_argument("--current", type=Path, default=DEFAULT_CURRENT)
    parser.add_argument("--releases-root", type=Path, default=DEFAULT_RELEASES_ROOT)
    parser.add_argument(
        "--unsafe-allow-active-current",
        action="store_true",
        help="emergency override allowing writes directly to active current",
    )
    parser.add_argument(
        "--slug",
        action="append",
        default=[],
        help="limit to a reviewed broken city slug; may be supplied multiple times",
    )
    args = parser.parse_args()

    if not args.root.is_dir():
        print(f"Root not found: {args.root}", file=sys.stderr)
        return 1
    if not CITIES_CSV.is_file() or not KEYWORDS_CSV.is_file():
        print("Required city/keyword CSV data is missing", file=sys.stderr)
        return 1

    requested = set(args.slug) if args.slug else set(BROKEN_CITY_SLUGS)
    unknown = requested - BROKEN_CITY_SLUGS
    if unknown:
        print(
            "Refusing slugs outside the reviewed BROKEN_CITY_SLUGS set: "
            + ", ".join(sorted(unknown)),
            file=sys.stderr,
        )
        return 2

    services = load_services()
    if not services:
        print("Keyword CSV produced zero renderable services; refusing rebuild.", file=sys.stderr)
        return 2

    plan: list[tuple[str, SimpleNamespace]] = []
    missing_cities: list[str] = []
    for slug in sorted(requested):
        city = load_city(slug)
        if city is None:
            missing_cities.append(slug)
        else:
            plan.append((slug, city))

    if missing_cities:
        print(
            "Reviewed broken city slug(s) missing/invalid in city CSV: "
            + ", ".join(missing_cities),
            file=sys.stderr,
        )
        return 2
    if not plan:
        print("Rebuild plan is empty; refusing no-op apply.", file=sys.stderr)
        return 2

    pages_per_city = 1 + len(services)
    print(
        f"plan: cities={len(plan)} services={len(services)} "
        f"pages_per_city={pages_per_city} total_pages={len(plan) * pages_per_city}"
    )
    for slug, city in plan:
        print(f"  {slug}: {city.name} -> {args.root / slug}")

    if not args.apply:
        print("[DRY-RUN] No files changed. Re-run against an isolated release candidate with --apply after review.")
        return 0

    target_error = mutation_target_error(
        args.root,
        current=args.current,
        releases_root=args.releases_root,
        allow_active_current=args.unsafe_allow_active_current,
    )
    if target_error:
        print(f"Refusing apply before directory/file writes: {target_error}", file=sys.stderr)
        return 3

    site = SimpleNamespace(id=1)
    total_built = 0
    try:
        for slug, city in plan:
            city_dir = args.root / slug
            city_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(city_dir, 0o755)

            hub_html = _render_city_hub_html(city, services)
            atomic_replace_text(city_dir / "index.html", hub_html)
            built = 1

            for service in services:
                service_dir = city_dir / service.slug
                service_dir.mkdir(parents=True, exist_ok=True)
                os.chmod(service_dir, 0o755)
                html = _render_html_landing(site, city, service)
                atomic_replace_text(service_dir / "index.html", html)
                built += 1

            total_built += built
            print(f"  {slug}: built {built} pages")
    except Exception as exc:  # noqa: BLE001
        print(
            f"Rebuild failed after partial candidate writes: {exc}. Discard/rebuild this release candidate.",
            file=sys.stderr,
        )
        return 4

    print(f"[APPLIED] total_pages_built={total_built}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
