#!/usr/bin/env python3
"""Re-render only the currently open index core.

- homepage: city grid/SEO links -> configured open cities
- open city hubs: configured open services + per-city whitelist extras
- closed hubs remain untouched and keep their noindex state

Safe by default: without ``--apply`` this command only prints the plan.
Rendering is routed through the hardened top-level content_generator facade.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

sys.path.insert(0, "/opt/p3-app")
os.chdir("/opt/p3-app")

from app.core.config import settings  # noqa: E402
from content_generator import (  # noqa: E402
    _render_city_hub_html,
    _homepage_cities,
    _template_env,
)

WEB_ROOT = Path("/var/www/x-gu.ru/current")
KEEP_CONFIG = Path("/opt/p3-app/data/index_keep_config.json")
WHITELIST = Path("/opt/p3-app/data/whitelist.txt")
CITIES_CSV = Path("/opt/p3-app/data/ru_cities_with_population.csv")
KEYWORDS_CSV = Path("/opt/p3-app/data/keywords_all.csv")


def load_config() -> tuple[list[str], list[str]]:
    if not KEEP_CONFIG.is_file():
        raise SystemExit(f"Missing required keep-config: {KEEP_CONFIG}")
    cfg = json.loads(KEEP_CONFIG.read_text(encoding="utf-8"))
    open_cities = cfg.get("open_cities") or []
    open_services = cfg.get("open_services") or []
    if not open_cities or not open_services:
        raise SystemExit("keep-config contains an empty open_cities/open_services set")
    return list(open_cities), list(open_services)


def whitelist_extras() -> dict[str, set[str]]:
    extras: dict[str, set[str]] = {}
    if not WHITELIST.is_file():
        return extras
    for line in WHITELIST.read_text(encoding="utf-8").splitlines():
        url = line.strip()
        if not url:
            continue
        parts = [part for part in urlparse(url).path.split("/") if part]
        if len(parts) == 2:
            extras.setdefault(parts[0], set()).add(parts[1])
    return extras


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


def load_services_map() -> dict:
    mapping = {}
    with KEYWORDS_CSV.open(encoding="utf-8") as file:
        for row in csv.DictReader(file):
            slug = (row.get("slug") or "").strip()
            if slug:
                mapping[slug] = SimpleNamespace(
                    name=(row.get("name") or "").strip(),
                    slug=slug,
                    niche=(row.get("niche") or "SEO").strip(),
                )
    return mapping


def build_plan() -> tuple[list[str], list[str], list[dict], list[tuple[SimpleNamespace, list[SimpleNamespace]]]]:
    open_cities, open_services = load_config()
    extras = whitelist_extras()
    city_map = load_city_map()
    service_map = load_services_map()

    open_set = set(open_cities)
    cities_for_home = [city for city in _homepage_cities() if city["slug"] in open_set]

    hub_plan: list[tuple[SimpleNamespace, list[SimpleNamespace]]] = []
    missing_cities: list[str] = []
    for slug in open_cities:
        city = city_map.get(slug)
        if city is None:
            missing_cities.append(slug)
            continue
        slugs_for_city = list(dict.fromkeys(open_services + sorted(extras.get(slug, set()))))
        services = [service_map[item] for item in slugs_for_city if item in service_map]
        hub_plan.append((city, services))
    return open_cities, missing_cities, cities_for_home, hub_plan


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="actually write rendered pages")
    parser.add_argument("--root", type=Path, default=WEB_ROOT)
    args = parser.parse_args()

    if not args.root.is_dir():
        raise SystemExit(f"Web root not found: {args.root}")
    if not CITIES_CSV.is_file() or not KEYWORDS_CSV.is_file():
        raise SystemExit("Required city/keyword CSV data is missing")

    open_cities, missing_cities, cities_for_home, hub_plan = build_plan()
    print(
        f"plan: configured_open_cities={len(open_cities)} homepage_cities={len(cities_for_home)} "
        f"hubs={len(hub_plan)} missing_cities={len(missing_cities)}"
    )
    if missing_cities:
        print("missing cities from CSV: " + ", ".join(missing_cities))

    if not args.apply:
        print("[DRY-RUN] No files changed. Re-run with --apply after reviewing the plan.")
        return 0

    env = _template_env()
    template = env.get_template("homepage_master.html.j2")
    html = template.render(
        base_domain=settings.base_domain,
        cities_json=json.dumps(cities_for_home, ensure_ascii=False),
    )
    home_file = args.root / "index.html"
    home_file.write_text(html, encoding="utf-8")
    os.chmod(home_file, 0o644)

    rendered = 0
    for city, services in hub_plan:
        hub_html = _render_city_hub_html(city, services)
        output = args.root / city.slug / "index.html"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(hub_html, encoding="utf-8")
        os.chmod(output, 0o644)
        rendered += 1

    print(f"[APPLIED] homepage=1 open_hubs={rendered}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
