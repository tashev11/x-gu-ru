#!/usr/bin/env python3
"""Re-render only the open index core of a self-contained release candidate."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse


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


WEB_ROOT = DEFAULT_CURRENT
RELEASE_KEEP_FILENAME = ".xgu-index-keep.json"
RELEASE_WHITELIST_FILENAME = ".xgu-whitelist.txt"
CITIES_CSV = APP_ROOT / "data/ru_cities_with_population.csv"
KEYWORDS_CSV = APP_ROOT / "data/keywords_all.csv"


def _valid_sha256(value: object) -> bool:
    digest = str(value or "").strip().lower()
    return len(digest) == 64 and all(char in "0123456789abcdef" for char in digest)


def load_config(path: Path, whitelist: Path) -> tuple[list[str], list[str]]:
    if not path.is_file():
        raise SystemExit(f"Missing release keep-config: {path}")
    if not whitelist.is_file():
        raise SystemExit(f"Missing release whitelist snapshot: {whitelist}")
    try:
        cfg = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Invalid release keep-config: {path}: {exc}") from exc
    if not isinstance(cfg, dict):
        raise SystemExit("release keep-config root must be a JSON object")

    open_cities = list(cfg.get("open_cities") or [])
    open_services = list(cfg.get("open_services") or [])
    if not open_cities or not open_services:
        raise SystemExit("release keep-config contains empty open_cities/open_services")
    if not str(cfg.get("policy_source") or "").strip() or not _valid_sha256(cfg.get("policy_sha256")):
        raise SystemExit("release keep-config has invalid policy provenance")
    if not str(cfg.get("whitelist_source") or "").strip() or not _valid_sha256(cfg.get("whitelist_sha256")):
        raise SystemExit("release keep-config has invalid whitelist provenance")

    expected = str(cfg.get("whitelist_sha256")).strip().lower()
    actual = hashlib.sha256(whitelist.read_bytes()).hexdigest()
    if actual != expected:
        raise SystemExit(f"release whitelist SHA-256 mismatch: manifest={expected} actual={actual}")
    return open_cities, open_services


def whitelist_extras(path: Path) -> dict[str, set[str]]:
    extras: dict[str, set[str]] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="strict").splitlines(), start=1):
        value = line.strip()
        if not value:
            continue
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.netloc != "x-gu.ru" or parsed.query or parsed.fragment:
            raise SystemExit(f"Invalid release whitelist URL on line {line_number}: {value}")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) == 2:
            extras.setdefault(parts[0], set()).add(parts[1])
        elif len(parts) > 2:
            raise SystemExit(f"Unsupported release whitelist path depth on line {line_number}: {value}")
    return extras


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


def load_services_map() -> dict[str, SimpleNamespace]:
    mapping: dict[str, SimpleNamespace] = {}
    with KEYWORDS_CSV.open(encoding="utf-8", errors="strict") as file:
        for row in csv.DictReader(file):
            slug = (row.get("slug") or "").strip()
            name = (row.get("name") or "").strip()
            if slug and name:
                mapping[slug] = SimpleNamespace(
                    name=name,
                    slug=slug,
                    niche=(row.get("niche") or "SEO").strip(),
                )
    return mapping


def build_plan(
    keep_config: Path,
    whitelist: Path,
) -> tuple[
    list[str],
    list[str],
    list[str],
    list[str],
    list[dict],
    list[tuple[SimpleNamespace, list[SimpleNamespace]]],
]:
    open_cities, open_services = load_config(keep_config, whitelist)
    extras = whitelist_extras(whitelist)
    city_map = load_city_map()
    service_map = load_services_map()

    missing_cities = [slug for slug in open_cities if slug not in city_map]
    missing_services = [slug for slug in open_services if slug not in service_map]
    whitelist_service_slugs = sorted({service for values in extras.values() for service in values})
    missing_whitelist_services = [slug for slug in whitelist_service_slugs if slug not in service_map]

    open_set = set(open_cities)
    cities_for_home = [city for city in _homepage_cities() if city["slug"] in open_set]

    hub_plan: list[tuple[SimpleNamespace, list[SimpleNamespace]]] = []
    for slug in open_cities:
        city = city_map.get(slug)
        if city is None:
            continue
        slugs_for_city = list(dict.fromkeys(open_services + sorted(extras.get(slug, set()))))
        services = [service_map[item] for item in slugs_for_city if item in service_map]
        hub_plan.append((city, services))

    return (
        open_cities,
        missing_cities,
        missing_services,
        missing_whitelist_services,
        cities_for_home,
        hub_plan,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="actually write rendered pages")
    parser.add_argument("--root", type=Path, default=WEB_ROOT)
    parser.add_argument("--current", type=Path, default=DEFAULT_CURRENT)
    parser.add_argument("--releases-root", type=Path, default=DEFAULT_RELEASES_ROOT)
    parser.add_argument(
        "--unsafe-allow-active-current",
        action="store_true",
        help="emergency override allowing writes directly to active current",
    )
    args = parser.parse_args()

    if not args.root.is_dir():
        raise SystemExit(f"Web root not found: {args.root}")
    if not CITIES_CSV.is_file() or not KEYWORDS_CSV.is_file():
        raise SystemExit("Required city/keyword CSV data is missing")

    release_root = args.root.resolve()
    keep_config = release_root / RELEASE_KEEP_FILENAME
    whitelist = release_root / RELEASE_WHITELIST_FILENAME

    # Content generator hooks read these dynamically when rendering.
    os.environ["XGU_KEEP_CONFIG"] = str(keep_config)
    os.environ["XGU_WHITELIST"] = str(whitelist)

    (
        open_cities,
        missing_cities,
        missing_services,
        missing_whitelist_services,
        cities_for_home,
        hub_plan,
    ) = build_plan(keep_config, whitelist)

    integrity_errors: list[str] = []
    if missing_cities:
        integrity_errors.append("missing cities from CSV: " + ", ".join(missing_cities))
    if missing_services:
        integrity_errors.append("missing configured services from CSV: " + ", ".join(missing_services))
    if missing_whitelist_services:
        integrity_errors.append("missing whitelist services from CSV: " + ", ".join(missing_whitelist_services))
    if len(hub_plan) != len(open_cities):
        integrity_errors.append("hub plan does not cover every open city")

    print(
        f"plan: configured_open_cities={len(open_cities)} homepage_cities={len(cities_for_home)} "
        f"hubs={len(hub_plan)} missing_cities={len(missing_cities)} "
        f"missing_services={len(missing_services)} missing_whitelist_services={len(missing_whitelist_services)}"
    )
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
        print(f"Refusing apply: {target_error}", file=sys.stderr)
        return 3

    try:
        env = _template_env()
        template = env.get_template("homepage_master.html.j2")
        home_html = template.render(
            base_domain=settings.base_domain,
            cities_json=json.dumps(cities_for_home, ensure_ascii=False),
        )
        atomic_replace_text(args.root / "index.html", home_html)

        rendered = 0
        for city, services in hub_plan:
            hub_html = _render_city_hub_html(city, services)
            output = args.root / city.slug / "index.html"
            if not output.parent.is_dir():
                raise RuntimeError(f"open city directory is missing from candidate: {output.parent}")
            atomic_replace_text(output, hub_html)
            rendered += 1
    except Exception as exc:  # noqa: BLE001
        print(f"Render failed: {exc}. Discard/rebuild this release candidate.", file=sys.stderr)
        return 4

    print(f"[APPLIED] homepage=1 open_hubs={rendered}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
