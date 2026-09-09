#!/usr/bin/env python3
"""Manage the indexable core of x-gu.ru.

Safe by default: the command only builds and prints a plan. Real changes to
robots meta, sitemap and keep-config require ``--apply``.

A JSON policy file may be supplied with ``--policy`` to avoid editing Python
for routine core changes. Format:

    {"open_cities": ["moskva", ...], "open_services": ["seo-audit-saita", ...]}

If no policy file is supplied, the historical built-in baseline below is used.
Whitelist URLs are always protected and a missing whitelist is a hard error.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

DEFAULT_WEB_ROOT = Path("/var/www/x-gu.ru/current")
DEFAULT_WHITELIST = Path("/opt/p3-app/data/whitelist.txt")
DEFAULT_KEEP_CONFIG = Path("/opt/p3-app/data/index_keep_config.json")
BASE = "https://x-gu.ru"

OPEN_CITIES = [
    "arkhangelsk", "astrakhan", "balakovo", "balashikha", "derbent", "groznyi",
    "iakutsk", "irkutsk", "izhevsk", "kaliningrad", "kirov", "kolomna",
    "krasnodar", "krasnoiarsk", "kursk", "lipetsk", "miass", "moskva",
    "naberezhnye-chelny", "neftekamsk", "nizhnevartovsk", "novosibirsk",
    "orel", "riazan", "samara", "sankt-peterburg", "sevastopol", "smolensk",
    "tiumen", "toliatti", "tomsk", "tver", "vladikavkaz", "vologda",
    "ekaterinburg", "kazan", "nizhnii-novgorod", "cheliabinsk", "ufa",
    "rostov-na-donu", "omsk", "voronezh", "perm", "volgograd",
    "podolsk", "belgorod", "serpukhov", "elektrostal", "odintsovo",
    "zelenograd", "ioshkar-ola", "piatigorsk", "barnaul", "maikop",
    "norilsk", "essentuki", "khasaviurt",
]

OPEN_SERVICES = [
    "prodvizhenie-saita", "seo-optimizatsiia-saita",
    "prodvizhenie-internet-magazina", "lokalnoe-prodvizhenie-saita",
    "sbor-semanticheskogo-iadra", "vyvod-saita-iz-pod-filtra",
    "ispravlenie-seo-oshibok",
    "sozdanie-saita", "sozdanie-lendinga", "sozdanie-internet-magazina",
    "sozdanie-saita-na-wordpress", "sozdanie-saita-na-tilde",
    "nastroika-reklamy", "nastroika-reklamy-v-iandeks-direkt",
    "nastroika-reklamy-v-google-ads", "mashtabirovanie-reklamnykh-kampanii",
    "seo-audit-saita", "audit-povedencheskikh-faktorov",
]

NOINDEX_TAG = '<meta name="robots" content="noindex, follow">'
INDEX_TAG = (
    '<meta name="robots" content="index,follow,max-image-preview:large,'
    'max-snippet:-1,max-video-preview:-1">'
)


def load_policy(path: Path | None) -> tuple[list[str], list[str]]:
    if path is None:
        return list(OPEN_CITIES), list(OPEN_SERVICES)
    if not path.is_file():
        raise SystemExit(f"Policy file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    cities = [str(x).strip() for x in payload.get("open_cities") or [] if str(x).strip()]
    services = [str(x).strip() for x in payload.get("open_services") or [] if str(x).strip()]
    if not cities or not services:
        raise SystemExit("Policy must contain non-empty open_cities and open_services")
    return list(dict.fromkeys(cities)), list(dict.fromkeys(services))


def url_for(html: Path, web_root: Path) -> str:
    rel = html.parent.relative_to(web_root)
    if str(rel) == ".":
        return f"{BASE}/"
    return f"{BASE}/{rel.as_posix()}/"


def build_keep_urls(whitelist: Path, open_cities: list[str], open_services: list[str]) -> set[str]:
    if not whitelist.is_file():
        raise SystemExit(
            f"Whitelist not found: {whitelist}. Refusing index-core changes because protected URLs are unknown."
        )

    keep: set[str] = set()
    for line in whitelist.read_text(encoding="utf-8").splitlines():
        url = line.strip()
        if url:
            keep.add(url if url.endswith("/") else url + "/")

    keep.add(f"{BASE}/")
    keep.add(f"{BASE}/privacy/")
    for city in open_cities:
        keep.add(f"{BASE}/{city}/")
        for service in open_services:
            keep.add(f"{BASE}/{city}/{service}/")
    return keep


def noindex_text(text: str) -> str | None:
    if 'name="robots" content="noindex' in text:
        return None
    new, count = re.subn(
        r'<meta name="robots" content="index,\s*follow[^"]*">',
        NOINDEX_TAG,
        text,
        count=1,
    )
    if count:
        return new
    new, count = re.subn(
        r'(<meta charset="[^"]+">)',
        r"\1\n    " + NOINDEX_TAG,
        text,
        count=1,
    )
    return new if count else None


def reopen_text(text: str) -> str | None:
    if 'name="robots" content="noindex' not in text:
        return None
    new, count = re.subn(
        r'<meta name="robots" content="noindex[^"]*">',
        INDEX_TAG,
        text,
        count=1,
    )
    return new if count else None


def make_plan(web_root: Path, keep: set[str]) -> tuple[list[tuple[Path, str]], int, int, int]:
    operations: list[tuple[Path, str]] = []
    scanned = kept = errors = 0

    for html in web_root.rglob("index.html"):
        scanned += 1
        try:
            text = html.read_text(encoding="utf-8")
            url = url_for(html, web_root)
            if url in keep:
                kept += 1
                if reopen_text(text) is not None:
                    operations.append((html, "reopen"))
            elif noindex_text(text) is not None:
                operations.append((html, "close"))
        except Exception as exc:  # noqa: BLE001
            errors += 1
            print(f"  ERROR {html}: {exc}", file=sys.stderr)

        if scanned % 5000 == 0:
            print(
                f"  ... scanned={scanned} kept={kept} "
                f"planned={len(operations)} errors={errors}",
                flush=True,
            )

    return operations, scanned, kept, errors


def apply_page_plan(operations: list[tuple[Path, str]]) -> tuple[int, int]:
    changed = errors = 0
    for path, action in operations:
        try:
            text = path.read_text(encoding="utf-8")
            new_text = reopen_text(text) if action == "reopen" else noindex_text(text)
            if new_text is None:
                continue
            path.write_text(new_text, encoding="utf-8")
            changed += 1
        except Exception as exc:  # noqa: BLE001
            errors += 1
            print(f"  APPLY ERROR {path}: {exc}", file=sys.stderr)
    return changed, errors


def write_keep_config(path: Path, open_cities: list[str], open_services: list[str]) -> None:
    payload = {
        "open_cities": open_cities,
        "open_services": open_services,
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_sitemap(web_root: Path, keep: set[str]) -> None:
    ts = time.strftime("%Y%m%d-%H%M%S")
    index_path = web_root / "sitemap.xml"
    shard_dir = web_root / "sitemaps"
    shard_dir.mkdir(parents=True, exist_ok=True)
    shard = shard_dir / "sitemap-1.xml"

    if index_path.exists():
        shutil.copy2(index_path, web_root / f"sitemap.xml.bak.{ts}")
    if shard.exists():
        shutil.copy2(shard, shard_dir / f"sitemap-1.xml.bak.{ts}")

    urls = sorted(keep)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    lines.extend(f"  <url><loc>{url}</loc></url>" for url in urls)
    lines.append("</urlset>")
    shard.write_text("\n".join(lines) + "\n", encoding="utf-8")

    index = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        f"  <sitemap><loc>{BASE}/sitemaps/sitemap-1.xml</loc></sitemap>",
        "</sitemapindex>",
    ]
    index_path.write_text("\n".join(index) + "\n", encoding="utf-8")
    print(f"sitemap rebuilt: {len(urls)} urls (backup suffix .bak.{ts})")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="apply robots/sitemap/config changes")
    parser.add_argument("--policy", type=Path, default=None, help="JSON open_cities/open_services policy")
    parser.add_argument("--web-root", type=Path, default=DEFAULT_WEB_ROOT)
    parser.add_argument("--whitelist", type=Path, default=DEFAULT_WHITELIST)
    parser.add_argument("--keep-config", type=Path, default=DEFAULT_KEEP_CONFIG)
    args = parser.parse_args()

    if not args.web_root.is_dir():
        raise SystemExit(f"Web root not found: {args.web_root}")

    open_cities, open_services = load_policy(args.policy)
    keep = build_keep_urls(args.whitelist, open_cities, open_services)
    print(
        f"policy: cities={len(open_cities)} services={len(open_services)} "
        f"protected/indexable URLs={len(keep)}"
    )

    operations, scanned, kept, errors = make_plan(args.web_root, keep)
    close_count = sum(1 for _, action in operations if action == "close")
    reopen_count = sum(1 for _, action in operations if action == "reopen")
    print(
        f"plan: scanned={scanned} kept_open={kept} close={close_count} "
        f"reopen={reopen_count} errors={errors}"
    )

    if errors:
        print("Plan contains read/errors; refusing to apply.", file=sys.stderr)
        return 2

    if not args.apply:
        print("[DRY-RUN] No files changed. Re-run with --apply after reviewing the plan.")
        return 0

    changed, apply_errors = apply_page_plan(operations)
    if apply_errors:
        print(
            f"Page application had {apply_errors} errors; sitemap/keep-config were NOT rewritten.",
            file=sys.stderr,
        )
        return 3

    write_keep_config(args.keep_config, open_cities, open_services)
    write_sitemap(args.web_root, keep)
    print(
        f"[APPLIED] page_changes={changed} keep_config={args.keep_config} "
        f"sitemap_urls={len(keep)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
