#!/usr/bin/env python3
"""Rebuild pages for cities whose deployment directory is empty.

These dirs exist on disk (drwx------ owned by root) but contain no HTML, so
the sitemap URLs return 404 (no file) or 403 (dir unreadable by www-data).
We regenerate hub + all keyword landings through the hardened public
content_generator facade, then set permissions so nginx can serve them.
"""
from __future__ import annotations

import csv
import os
import sys
from pathlib import Path
from types import SimpleNamespace

# Allow importing the canonical /opt/p3-app/content_generator.py facade.
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
    with CITIES_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if (row.get("slug") or "").strip() == slug:
                return SimpleNamespace(
                    slug=slug,
                    name=(row.get("city") or "").strip(),
                    region=(row.get("region") or "Россия").strip(),
                )
    return None


def load_services() -> list[SimpleNamespace]:
    services: list[SimpleNamespace] = []
    with KEYWORDS_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = (row.get("name") or "").strip()
            slug = (row.get("slug") or "").strip()
            niche = (row.get("niche") or "SEO").strip()
            if name and slug:
                services.append(SimpleNamespace(name=name, slug=slug, niche=niche))
    return services


def chmod_recursive(path: Path, dir_mode: int = 0o755, file_mode: int = 0o644) -> None:
    """Set permissions so nginx (www-data) can read."""
    try:
        os.chmod(path, dir_mode if path.is_dir() else file_mode)
    except OSError as e:
        print(f"  chmod fail {path}: {e}")
    if path.is_dir():
        for child in path.iterdir():
            chmod_recursive(child, dir_mode, file_mode)


def main() -> int:
    services = load_services()
    print(f"Loaded {len(services)} services")
    site = SimpleNamespace(id=1)

    total_built = 0
    for slug in sorted(BROKEN_CITY_SLUGS):
        city = load_city(slug)
        if not city:
            print(f"  SKIP {slug}: not found in CSV")
            continue
        city_dir = PUBLIC_ROOT / slug
        city_dir.mkdir(parents=True, exist_ok=True)

        hub_html = _render_city_hub_html(city, services)
        (city_dir / "index.html").write_text(hub_html, encoding="utf-8")
        built = 1

        for service in services:
            sdir = city_dir / service.slug
            sdir.mkdir(parents=True, exist_ok=True)
            html = _render_html_landing(site, city, service)
            (sdir / "index.html").write_text(html, encoding="utf-8")
            built += 1

        chmod_recursive(city_dir)

        total_built += built
        print(f"  {slug}: built {built} pages (1 hub + {len(services)} services)")

    print(f"\n[DONE] total_pages_built={total_built}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
