#!/usr/bin/env python3
"""Rebuild pages for a reviewed set of broken city directories.

Physical pages may be rebuilt for repair purposes, but an indexable city hub
links only to services allowed by the release policy for that exact city plus
protected whitelist exceptions.
"""
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

from app.services.content_generator import _render_city_hub_html, _render_html_landing  # noqa: E402
from app.services.index_policy import normalize_policy_payload, page_is_open, services_for_city  # noqa: E402
from release_safety import (  # noqa: E402
    DEFAULT_CURRENT,
    DEFAULT_RELEASES_ROOT,
    atomic_replace_text,
    mutation_target_error,
)


PUBLIC_ROOT = DEFAULT_CURRENT
KEYWORDS_CSV = APP_ROOT / "data/keywords_all.csv"
CITIES_CSV = APP_ROOT / "data/ru_cities_with_population.csv"
MANIFEST_NAME = ".xgu-index-keep.json"
WHITELIST_NAME = ".xgu-whitelist.txt"

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


def _valid_sha256(value: object) -> bool:
    digest = str(value or "").strip().lower()
    return len(digest) == 64 and all(char in "0123456789abcdef" for char in digest)


def load_city(slug: str) -> SimpleNamespace | None:
    with CITIES_CSV.open(encoding="utf-8", errors="strict") as file:
        for row in csv.DictReader(file):
            if (row.get("slug") or "").strip() == slug:
                name = (row.get("city") or "").strip()
                if not name:
                    return None
                return SimpleNamespace(slug=slug, name=name, region=(row.get("region") or "Россия").strip())
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


def whitelist_extras(path: Path) -> dict[str, set[str]]:
    extras: dict[str, set[str]] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="strict").splitlines(), start=1):
        value = line.strip()
        if not value:
            continue
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.netloc != "x-gu.ru" or parsed.query or parsed.fragment:
            raise ValueError(f"invalid release whitelist URL on line {line_number}: {value}")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) == 2:
            extras.setdefault(parts[0], set()).add(parts[1])
        elif len(parts) > 2:
            raise ValueError(f"unsupported release whitelist path depth on line {line_number}: {value}")
    return extras


def validate_release_contract(root: Path) -> tuple[Path | None, Path | None, dict | None, list[str]]:
    errors: list[str] = []
    manifest = root.resolve() / MANIFEST_NAME
    whitelist = root.resolve() / WHITELIST_NAME
    if not manifest.is_file():
        errors.append(f"release policy manifest missing: {manifest}")
    if not whitelist.is_file():
        errors.append(f"release whitelist snapshot missing: {whitelist}")
    if errors:
        return (manifest if manifest.is_file() else None, whitelist if whitelist.is_file() else None, None, errors)

    try:
        payload = json.loads(manifest.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return manifest, whitelist, None, [f"release policy manifest is invalid JSON: {exc}"]
    if not isinstance(payload, dict):
        return manifest, whitelist, None, ["release policy manifest root must be a JSON object"]
    try:
        policy = normalize_policy_payload(payload)
    except ValueError as exc:
        return manifest, whitelist, None, [f"release index policy is invalid: {exc}"]

    if not str(payload.get("policy_source") or "").strip() or not _valid_sha256(payload.get("policy_sha256")):
        errors.append("release policy manifest has invalid policy provenance")
    if not str(payload.get("whitelist_source") or "").strip() or not _valid_sha256(payload.get("whitelist_sha256")):
        errors.append("release policy manifest has invalid whitelist provenance")

    if whitelist.is_file() and _valid_sha256(payload.get("whitelist_sha256")):
        expected = str(payload.get("whitelist_sha256")).strip().lower()
        actual = hashlib.sha256(whitelist.read_bytes()).hexdigest()
        if actual != expected:
            errors.append(f"release whitelist SHA-256 mismatch: manifest={expected} actual={actual}")
    return manifest, whitelist, policy, errors


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
    parser.add_argument("--slug", action="append", default=[], help="reviewed broken city slug; may be repeated")
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
        print("Refusing slugs outside reviewed set: " + ", ".join(sorted(unknown)), file=sys.stderr)
        return 2

    services = load_services()
    if not services:
        print("Keyword CSV produced zero renderable services; refusing rebuild.", file=sys.stderr)
        return 2
    services_by_slug = {service.slug: service for service in services}

    plan: list[tuple[str, SimpleNamespace]] = []
    missing_cities: list[str] = []
    for slug in sorted(requested):
        city = load_city(slug)
        if city is None:
            missing_cities.append(slug)
        else:
            plan.append((slug, city))
    if missing_cities:
        print("Reviewed broken city slug(s) missing/invalid in city CSV: " + ", ".join(missing_cities), file=sys.stderr)
        return 2
    if not plan:
        print("Rebuild plan is empty; refusing no-op apply.", file=sys.stderr)
        return 2

    manifest, whitelist, policy, contract_errors = validate_release_contract(args.root)
    if contract_errors:
        for error in contract_errors:
            print(f"  ERROR: {error}", file=sys.stderr)
        print("Refusing rebuild without a valid self-contained release contract.", file=sys.stderr)
        return 3
    assert manifest is not None and whitelist is not None and policy is not None

    try:
        extras = whitelist_extras(whitelist)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 3

    required_hub_services = {
        service_slug
        for slug, _city in plan
        for service_slug in (services_for_city(policy, slug) + sorted(extras.get(slug, set())))
    }
    missing_required_services = sorted(required_hub_services - set(services_by_slug))
    if missing_required_services:
        print("Policy/whitelist service(s) missing from keyword CSV: " + ", ".join(missing_required_services), file=sys.stderr)
        return 3

    pages_per_city = 1 + len(services)
    print(
        f"plan: policy_v={policy['policy_version']} mode={policy['policy_mode']} cities={len(plan)} "
        f"physical_services={len(services)} pages_per_city={pages_per_city} total_pages={len(plan) * pages_per_city}"
    )
    for slug, city in plan:
        hub_links = services_for_city(policy, slug) if page_is_open(policy, slug) else []
        hub_links = list(dict.fromkeys(hub_links + sorted(extras.get(slug, set()))))
        print(f"  {slug}: {city.name} -> {args.root / slug} | open_hub_links={len(hub_links)}")

    if not args.apply:
        print("[DRY-RUN] No files changed. Re-run against this release candidate with --apply after review.")
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

    os.environ["XGU_KEEP_CONFIG"] = str(manifest)
    os.environ["XGU_WHITELIST"] = str(whitelist)

    site = SimpleNamespace(id=1)
    total_built = 0
    try:
        for slug, city in plan:
            city_dir = args.root / slug
            city_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(city_dir, 0o755)

            if page_is_open(policy, slug):
                hub_slugs = list(dict.fromkeys(services_for_city(policy, slug) + sorted(extras.get(slug, set()))))
                hub_services = [services_by_slug[item] for item in hub_slugs]
            else:
                hub_services = services
            hub_html = _render_city_hub_html(city, hub_services)
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
            print(f"  {slug}: built {built} physical pages")
    except Exception as exc:  # noqa: BLE001
        print(
            f"Rebuild failed after partial candidate writes: {exc}. Discard/rebuild this release candidate.",
            file=sys.stderr,
        )
        return 4

    print(f"[APPLIED] total_pages_built={total_built} policy_manifest={manifest} whitelist_snapshot={whitelist}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
