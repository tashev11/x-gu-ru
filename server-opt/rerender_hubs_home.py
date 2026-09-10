#!/usr/bin/env python3
"""Re-render homepage and discovered city hubs in a release candidate.

The candidate must contain ``.xgu-index-keep.json``. The generator is explicitly
pointed at that manifest so the render cannot inherit robots/index policy from
the currently active release.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace


APP_ROOT = Path("/opt/p3-app")
sys.path.insert(0, str(APP_ROOT))
os.chdir(APP_ROOT)

from app.core.config import settings  # noqa: E402
from content_generator import _homepage_cities, _render_city_hub_html, _template_env  # noqa: E402
from release_safety import (  # noqa: E402
    DEFAULT_CURRENT,
    DEFAULT_RELEASES_ROOT,
    atomic_replace_text,
    mutation_target_error,
)


PUBLIC_ROOT = DEFAULT_CURRENT
RELEASE_KEEP_FILENAME = ".xgu-index-keep.json"
DEFAULT_WHITELIST = APP_ROOT / "data/whitelist.txt"
CITIES_CSV = APP_ROOT / "data/ru_cities_with_population.csv"
KEYWORDS_CSV = APP_ROOT / "data/keywords_all.csv"


def load_city_map() -> dict[str, SimpleNamespace]:
    mapping: dict[str, SimpleNamespace] = {}
    with CITIES_CSV.open(encoding="utf-8", errors="strict") as file:
        for row in csv.DictReader(file):
            slug = (row.get("slug") or "").strip()
            name = (row.get("city") or "").strip()
            if slug and name:
                mapping[slug] = SimpleNamespace(
                    slug=slug,
                    name=name,
                    region=(row.get("region") or "Россия").strip(),
                )
    return mapping


def load_services() -> list[SimpleNamespace]:
    services: list[SimpleNamespace] = []
    with KEYWORDS_CSV.open(encoding="utf-8", errors="strict") as file:
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


def discover_hubs(root: Path, city_map: dict[str, SimpleNamespace]) -> tuple[list[tuple[Path, SimpleNamespace]], list[str]]:
    hubs: list[tuple[Path, SimpleNamespace]] = []
    skipped: list[str] = []
    for name in sorted(os.listdir(root)):
        city_dir = root / name
        if not city_dir.is_dir() or name in {"sitemaps", "assets", "privacy", ".well-known"} or name.startswith("."):
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
    parser.add_argument("--current", type=Path, default=DEFAULT_CURRENT)
    parser.add_argument("--releases-root", type=Path, default=DEFAULT_RELEASES_ROOT)
    parser.add_argument("--whitelist", type=Path, default=DEFAULT_WHITELIST)
    parser.add_argument(
        "--unsafe-allow-active-current",
        action="store_true",
        help="emergency override allowing writes directly to active current",
    )
    args = parser.parse_args()

    if not args.root.is_dir():
        raise SystemExit(f"Public root not found: {args.root}")
    if not CITIES_CSV.is_file() or not KEYWORDS_CSV.is_file():
        raise SystemExit("Required city/keyword CSV data is missing")

    keep_config = args.root.resolve() / RELEASE_KEEP_FILENAME
    if not keep_config.is_file():
        raise SystemExit(
            f"Release policy manifest missing: {keep_config}. Run shrink_index.py on the candidate first."
        )
    if not args.whitelist.is_file():
        raise SystemExit(f"Whitelist missing: {args.whitelist}")
    os.environ["XGU_KEEP_CONFIG"] = str(keep_config)
    os.environ["XGU_WHITELIST"] = str(args.whitelist.resolve())

    city_map = load_city_map()
    services = load_services()
    hubs, skipped = discover_hubs(args.root, city_map)
    print(f"plan: homepage=1 hubs={len(hubs)} skipped={len(skipped)} services={len(services)}")

    integrity_errors: list[str] = []
    if not services:
        integrity_errors.append("service CSV produced zero renderable services")
    if skipped:
        integrity_errors.append("city dirs missing from CSV: " + ", ".join(skipped[:20]))
    for error in integrity_errors:
        print(f"  ERROR: {error}", file=sys.stderr)

    if not args.apply:
        print("[DRY-RUN] No files changed. Fix integrity errors, then re-run against this candidate with --apply.")
        return 0 if not integrity_errors else 2
    if integrity_errors:
        print("Refusing apply while render inputs are incomplete.", file=sys.stderr)
        return 2

    target_error = mutation_target_error(
        args.root,
        current=args.current,
        releases_root=args.releases_root,
        allow_active_current=args.unsafe_allow_active_current,
    )
    if target_error:
        print(f"Refusing apply before render/write: {target_error}", file=sys.stderr)
        return 3

    try:
        env = _template_env()
        home_tpl = env.get_template("homepage_master.html.j2")
        home_html = home_tpl.render(
            base_domain=settings.base_domain,
            cities_json=json.dumps(_homepage_cities(), ensure_ascii=False),
        )
        atomic_replace_text(args.root / "index.html", home_html)

        rendered = 0
        for hub_file, city in hubs:
            html = _render_city_hub_html(city, services)
            atomic_replace_text(hub_file, html)
            rendered += 1
            if rendered % 50 == 0:
                print(f"  ... {rendered} hubs re-rendered", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"Render failed: {exc}. Discard/rebuild this release candidate.", file=sys.stderr)
        return 4

    print(f"[APPLIED] homepage=1 hubs={rendered} skipped=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
