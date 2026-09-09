#!/usr/bin/env python3
"""Step 4 of the index shrink: remove links to closed pages from navigation.

- homepage: city grid + hidden SEO links -> only the 44 OPEN cities
- the 44 open hubs: service chips -> only the 18 OPEN services, plus any
  whitelist service of that specific city (so pages that already rank keep
  their internal link)
Closed hubs (131 cities) are left as-is: they are noindexed anyway.
"""
from __future__ import annotations

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
from app.services.content_generator import (  # noqa: E402
    _render_city_hub_html,
    _homepage_cities,
    _template_env,
)

WEB_ROOT = Path("/var/www/x-gu.ru/current")
KEEP_CONFIG = Path("/opt/p3-app/data/index_keep_config.json")
WHITELIST = Path("/opt/p3-app/data/whitelist.txt")
CITIES_CSV = Path("/opt/p3-app/data/ru_cities_with_population.csv")
KEYWORDS_CSV = Path("/opt/p3-app/data/keywords_all.csv")


def load_config():
    cfg = json.loads(KEEP_CONFIG.read_text(encoding="utf-8"))
    return cfg["open_cities"], cfg["open_services"]


def whitelist_extras() -> dict[str, set[str]]:
    """city_slug -> set of service slugs present in whitelist for that city."""
    extras: dict[str, set[str]] = {}
    for line in WHITELIST.read_text(encoding="utf-8").splitlines():
        u = line.strip()
        if not u:
            continue
        parts = [p for p in urlparse(u).path.split("/") if p]
        if len(parts) == 2:
            extras.setdefault(parts[0], set()).add(parts[1])
    return extras


def load_city_map() -> dict:
    m = {}
    with CITIES_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            slug = (row.get("slug") or "").strip()
            if slug:
                m[slug] = SimpleNamespace(
                    slug=slug,
                    name=(row.get("city") or "").strip(),
                    region=(row.get("region") or "Россия").strip(),
                )
    return m


def load_services_map() -> dict:
    m = {}
    with KEYWORDS_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            slug = (row.get("slug") or "").strip()
            if slug:
                m[slug] = SimpleNamespace(
                    name=(row.get("name") or "").strip(),
                    slug=slug,
                    niche=(row.get("niche") or "SEO").strip(),
                )
    return m


def main() -> int:
    open_cities, open_services = load_config()
    extras = whitelist_extras()
    city_map = load_city_map()
    svc_map = load_services_map()

    # homepage with only open cities
    open_set = set(open_cities)
    cities_for_home = [c for c in _homepage_cities() if c["slug"] in open_set]
    env = _template_env()
    tpl = env.get_template("homepage_master.html.j2")
    html = tpl.render(
        base_domain=settings.base_domain,
        cities_json=json.dumps(cities_for_home, ensure_ascii=False),
    )
    (WEB_ROOT / "index.html").write_text(html, encoding="utf-8")
    os.chmod(WEB_ROOT / "index.html", 0o644)
    print(f"homepage: {len(cities_for_home)} cities in grid")

    # open hubs with filtered services
    hubs = 0
    for slug in open_cities:
        city = city_map.get(slug)
        if city is None:
            print(f"  SKIP {slug}: not in cities csv")
            continue
        slugs_for_city = list(dict.fromkeys(open_services + sorted(extras.get(slug, set()))))
        services = [svc_map[s] for s in slugs_for_city if s in svc_map]
        hub_html = _render_city_hub_html(city, services)
        out = WEB_ROOT / slug / "index.html"
        out.write_text(hub_html, encoding="utf-8")
        os.chmod(out, 0o644)
        hubs += 1

    print(f"[DONE] homepage=1 open_hubs={hubs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
