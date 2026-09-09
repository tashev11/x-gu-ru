#!/usr/bin/env python3
"""Re-render the homepage and city hub pages.

Safe by default: the command prints the pages it would touch. Real writes need
``--apply``. Service landing pages are not modified.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, "/opt/p3-app")
os.chdir("/opt/p3-app")

from app.core.config import settings  # noqa: E402
from content_generator import (  # noqa: E402
    _render_city_hub_html,
    _homepage_cities,
    _template_env,
)

PUBLIC_ROOT = Path("/var/www/x-gu.ru/current")
CITIES_CSV = Path("/opt/p3-app/data/ru_cities_with_population.csv")
KEYWORDS_CSV = Path("/opt/p3-app/data/keywords_all.csv")


def load_city_map() -> dict:
    mapping = {}
    with CITIES_CSV.open(encoding="utf-8") as file:
        for row in csv.DictReader(file):
            slug = (row.get("slug") or "").strip()
            if slug:
                mapping[slug] = SimpleNamespace(
                    slug=slug,
                    name=(row.get("city") or "").strip(),
                    region=(row.get("region") or "Россия").strip(),
                )
    return mapping


def load_services() -> list:
    services = []
    with KEYWORDS_CSV.open(encoding="utf-8") as file:
        for row in csv.DictReader(file):
            name = (row.get("name") or "").strip()
            slug = (row.get("slug") or "").strip()
            if name and slug:
                services.append(
                    SimpleNamespace(
                        name=name,
                        slug=slug,
                        niche=(row.get("niche") or "SEO").strip(),
                    )
                )
    return services


def discover_hubs(root: Path, city_map: dict) -> tuple[list[tuple[Path, object]], list[str]]:
    hubs: list[tuple[Path, object]] = []
    skipped: list[str] = []
    for name in sorted(os.listdir(root)):
        city_dir = root / name
        if not city_dir.is_dir() or name == "sitemaps" or name.startswith("."):
            continue
        hub_file = city_dir / "index.html"
        if not hub_file.is_file():
            continue
        if not any((city_dir / child).is_dir() for child in os.listdir(city_dir)):
            continue
        city = city_map.get(name)
        if city is None:
            skipped.append(name)
            continue
        hubs.append((hub_file, city))
    return hubs, skipped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="actually write rendered pages")
    parser.add_argument("--root", type=Path, default=PUBLIC_ROOT)
    args = parser.parse_args()

    if not args.root.is_dir():
        raise SystemExit(f"Public root not found: {args.root}")
    if not CITIES_CSV.is_file() or not KEYWORDS_CSV.is_file():
        raise SystemExit("Required city/keyword CSV data is missing")

    city_map = load_city_map()
    services = load_services()
    hubs, skipped = discover_hubs(args.root, city_map)
    print(
        f"plan: homepage=1 hubs={len(hubs)} skipped={len(skipped)} "
        f"services={len(services)}"
    )
    if skipped:
        print("skipped city dirs not in CSV: " + ", ".join(skipped[:20]))

    if not args.apply:
        print("[DRY-RUN] No files changed. Re-run with --apply after reviewing the plan.")
        return 0

    env = _template_env()
    home_tpl = env.get_template("homepage_master.html.j2")
    home_html = home_tpl.render(
        base_domain=settings.base_domain,
        cities_json=json.dumps(_homepage_cities(), ensure_ascii=False),
    )
    home_file = args.root / "index.html"
    home_file.write_text(home_html, encoding="utf-8")
    os.chmod(home_file, 0o644)

    rendered = 0
    for hub_file, city in hubs:
        html = _render_city_hub_html(city, services)
        hub_file.write_text(html, encoding="utf-8")
        os.chmod(hub_file, 0o644)
        rendered += 1
        if rendered % 50 == 0:
            print(f"  ... {rendered} hubs re-rendered", flush=True)

    print(f"[APPLIED] homepage=1 hubs={rendered} skipped={len(skipped)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
