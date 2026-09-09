#!/usr/bin/env python3
"""Shrink the indexable core of x-gu.ru.

Keep OPEN (index,follow + in sitemap):
  - every URL from whitelist.txt (Yandex in-search pages) — untouchable
  - homepage /, /privacy/
  - hub pages for OPEN_CITIES
  - service pages OPEN_CITIES x OPEN_SERVICES

Everything else: <meta name="robots" content="noindex, follow"> and removed
from sitemap. Pages are NOT deleted.

Also saves the keep-config to /opt/p3-app/data/index_keep_config.json so
future re-render scripts can preserve the shrink.
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

WEB_ROOT = Path("/var/www/x-gu.ru/current")
WHITELIST = Path("/opt/p3-app/data/whitelist.txt")
KEEP_CONFIG = Path("/opt/p3-app/data/index_keep_config.json")
BASE = "https://x-gu.ru"

OPEN_CITIES = [
    # 34 from Yandex whitelist
    "arkhangelsk", "astrakhan", "balakovo", "balashikha", "derbent", "groznyi",
    "iakutsk", "irkutsk", "izhevsk", "kaliningrad", "kirov", "kolomna",
    "krasnodar", "krasnoiarsk", "kursk", "lipetsk", "miass", "moskva",
    "naberezhnye-chelny", "neftekamsk", "nizhnevartovsk", "novosibirsk",
    "orel", "riazan", "samara", "sankt-peterburg", "sevastopol", "smolensk",
    "tiumen", "toliatti", "tomsk", "tver", "vladikavkaz", "vologda",
    # +10 millionniki approved by owner
    "ekaterinburg", "kazan", "nizhnii-novgorod", "cheliabinsk", "ufa",
    "rostov-na-donu", "omsk", "voronezh", "perm", "volgograd",
    # +13 cities Google already surfaces on multiple pages (GSC 90d audit,
    # 2026-08-17): each has 3-23 pages with impressions, best positions 3-61.
    # Yandex-only whitelist had missed them.
    "podolsk", "belgorod", "serpukhov", "elektrostal", "odintsovo",
    "zelenograd", "ioshkar-ola", "piatigorsk", "barnaul", "maikop",
    "norilsk", "essentuki", "khasaviurt",
]

OPEN_SERVICES = [
    # SEO (7)
    "prodvizhenie-saita", "seo-optimizatsiia-saita",
    "prodvizhenie-internet-magazina", "lokalnoe-prodvizhenie-saita",
    "sbor-semanticheskogo-iadra", "vyvod-saita-iz-pod-filtra",
    "ispravlenie-seo-oshibok",
    # WEBDEV (5)
    "sozdanie-saita", "sozdanie-lendinga", "sozdanie-internet-magazina",
    "sozdanie-saita-na-wordpress", "sozdanie-saita-na-tilde",
    # ADS (4)
    "nastroika-reklamy", "nastroika-reklamy-v-iandeks-direkt",
    "nastroika-reklamy-v-google-ads", "mashtabirovanie-reklamnykh-kampanii",
    # AUDIT (2)
    "seo-audit-saita", "audit-povedencheskikh-faktorov",
]

ROBOTS_OPEN_MARKERS = ('content="index,follow', 'content="index, follow')
NOINDEX_TAG = '<meta name="robots" content="noindex, follow">'


def url_for(html: Path) -> str:
    rel = html.parent.relative_to(WEB_ROOT)
    if str(rel) == ".":
        return f"{BASE}/"
    return f"{BASE}/{rel.as_posix()}/"


def build_keep_urls() -> set[str]:
    keep: set[str] = set()
    # whitelist — absolute protection
    for line in WHITELIST.read_text(encoding="utf-8").splitlines():
        u = line.strip()
        if u:
            keep.add(u if u.endswith("/") else u + "/")
    keep.add(f"{BASE}/")
    keep.add(f"{BASE}/privacy/")
    for c in OPEN_CITIES:
        keep.add(f"{BASE}/{c}/")
        for s in OPEN_SERVICES:
            keep.add(f"{BASE}/{c}/{s}/")
    return keep


INDEX_TAG = ('<meta name="robots" content="index,follow,max-image-preview:large,'
             'max-snippet:-1,max-video-preview:-1">')


def patch_noindex(html: Path) -> bool:
    """Switch robots meta to noindex,follow. Returns True if changed."""
    text = html.read_text(encoding="utf-8")
    if 'name="robots" content="noindex' in text:
        return False  # already closed
    import re
    new, n = re.subn(
        r'<meta name="robots" content="index,\s*follow[^"]*">',
        NOINDEX_TAG,
        text,
        count=1,
    )
    if n == 0:
        # page without a robots meta: inject noindex right after <head> charset line
        new, n = re.subn(
            r'(<meta charset="[^"]+">)',
            r"\1\n    " + NOINDEX_TAG,
            text,
            count=1,
        )
        if n == 0:
            return False
    html.write_text(new, encoding="utf-8")
    return True


def patch_reopen(html: Path) -> bool:
    """Re-open a page that was previously closed (noindex -> index).
    Needed when the open core is widened. Returns True if changed."""
    text = html.read_text(encoding="utf-8")
    if 'name="robots" content="noindex' not in text:
        return False  # already open
    import re
    new, n = re.subn(
        r'<meta name="robots" content="noindex[^"]*">',
        INDEX_TAG,
        text,
        count=1,
    )
    if n == 0:
        return False
    html.write_text(new, encoding="utf-8")
    return True


def write_sitemap(keep: set[str]) -> None:
    ts = time.strftime("%Y%m%d-%H%M%S")
    # backups (step-3 requirement)
    shutil.copy2(WEB_ROOT / "sitemap.xml", WEB_ROOT / f"sitemap.xml.bak.{ts}")
    shard = WEB_ROOT / "sitemaps" / "sitemap-1.xml"
    if shard.exists():
        shutil.copy2(shard, WEB_ROOT / "sitemaps" / f"sitemap-1.xml.bak.{ts}")

    urls = sorted(keep)
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u in urls:
        lines.append(f"  <url><loc>{u}</loc></url>")
    lines.append("</urlset>")
    shard.write_text("\n".join(lines) + "\n", encoding="utf-8")

    index = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
             f"  <sitemap><loc>{BASE}/sitemaps/sitemap-1.xml</loc></sitemap>",
             "</sitemapindex>"]
    (WEB_ROOT / "sitemap.xml").write_text("\n".join(index) + "\n", encoding="utf-8")
    print(f"sitemap rebuilt: {len(urls)} urls (backup .bak.{ts})")


def main() -> int:
    keep = build_keep_urls()
    print(f"keep set: {len(keep)} urls")

    KEEP_CONFIG.write_text(json.dumps({
        "open_cities": OPEN_CITIES,
        "open_services": OPEN_SERVICES,
        "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"keep config saved: {KEEP_CONFIG}")

    write_sitemap(keep)

    scanned = closed = kept = reopened = errors = 0
    for html in WEB_ROOT.rglob("index.html"):
        scanned += 1
        url = url_for(html)
        try:
            if url in keep:
                kept += 1
                if patch_reopen(html):  # core widened -> bring it back
                    reopened += 1
            else:
                if patch_noindex(html):
                    closed += 1
        except Exception as e:  # noqa: BLE001
            errors += 1
            print(f"  ERROR {html}: {e}", file=sys.stderr)
        if scanned % 5000 == 0:
            print(f"  ... scanned={scanned} kept={kept} closed={closed} reopened={reopened} errors={errors}", flush=True)

    print(f"[DONE] scanned={scanned} kept_open={kept} noindexed={closed} reopened={reopened} errors={errors}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
