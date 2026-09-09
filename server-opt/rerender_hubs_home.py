#!/usr/bin/env python3
"""Re-render the homepage + all city hub pages with the new design
(grouped service chips by category; Cyrillic city grid).

Only touches hub index.html (city_dir/index.html) and the site root index.html.
Service landing pages are untouched. Overwrites in place (no net disk growth).
"""
from __future__ import annotations

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


def load_services() -> list:
    out = []
    with KEYWORDS_CSV.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = (row.get("name") or "").strip()
            slug = (row.get("slug") or "").strip()
            if name and slug:
                out.append(SimpleNamespace(name=name, slug=slug, niche=(row.get("niche") or "SEO").strip()))
    return out


def main() -> int:
    city_map = load_city_map()
    services = load_services()
    print(f"cities in csv={len(city_map)} services={len(services)}")

    env = _template_env()
    home_tpl = env.get_template("homepage_master.html.j2")
    home_html = home_tpl.render(
        base_domain=settings.base_domain,
        cities_json=json.dumps(_homepage_cities(), ensure_ascii=False),
    )
    (PUBLIC_ROOT / "index.html").write_text(home_html, encoding="utf-8")
    os.chmod(PUBLIC_ROOT / "index.html", 0o644)
    print("homepage re-rendered")

    hubs = skipped = 0
    for d in sorted(os.listdir(PUBLIC_ROOT)):
        cdir = PUBLIC_ROOT / d
        if not cdir.is_dir() or d in ("sitemaps",) or d.startswith("."):
            continue
        hub_file = cdir / "index.html"
        if not hub_file.is_file():
            continue
        has_sub = any((cdir / x).is_dir() for x in os.listdir(cdir))
        if not has_sub:
            continue
        city = city_map.get(d)
        if city is None:
            skipped += 1
            print(f"  skip {d}: not in cities csv")
            continue
        html = _render_city_hub_html(city, services)
        hub_file.write_text(html, encoding="utf-8")
        os.chmod(hub_file, 0o644)
        hubs += 1
        if hubs % 50 == 0:
            print(f"  ... {hubs} hubs re-rendered", flush=True)

    print(f"[DONE] homepage=1 hubs={hubs} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
