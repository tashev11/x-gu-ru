#!/usr/bin/env python3
"""Re-render only the currently open index core.

- homepage: city grid/SEO links -> configured open cities
- open city hubs: configured open services + per-city whitelist extras
- closed hubs remain untouched and keep their noindex state

Rendering is routed through the hardened top-level content_generator facade.
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


def load_config():
    if not KEEP_CONFIG.is_file():
        raise SystemExit(f"Missing required keep-config: {KEEP_CONFIG}")
    cfg = json.loads(KEEP_CONFIG.read_text(encoding="utf-8"))
    open_cities = cfg.get("open_cities") or []
    open_services = cfg.get("open_services") or []
    if not open_cities or not open_services:
        raise SystemExit("keep-config contains an empty open_cities/open_services set")
    return open_cities, open_services


def whitelist_extras() -> dict[str, set[str]]:
    """city_slug -> service slugs explicitly protected by whitelist."""
    extras: dict[str, set[str]] = {}
    if not WHITELIST.is_file():
        return extras
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
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(hub_html, encoding="utf-8")
        os.chmod(out, 0o644)
        hubs += 1

    print(f"[DONE] homepage=1 open_hubs={hubs}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
