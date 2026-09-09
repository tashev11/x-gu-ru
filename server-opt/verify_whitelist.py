#!/usr/bin/env python3
"""Acceptance check for the index shrink:
for EVERY whitelist URL verify
  1) the page file exists
  2) its robots meta does NOT contain noindex
  3) it is present in the new sitemap
Prints a per-URL table and a final verdict. Exit 1 on any failure.
"""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

WEB_ROOT = Path("/var/www/x-gu.ru/current")
WHITELIST = Path("/opt/p3-app/data/whitelist.txt")
SITEMAP_SHARD = WEB_ROOT / "sitemaps" / "sitemap-1.xml"


def file_for(url: str) -> Path:
    path = urlparse(url).path.strip("/")
    return WEB_ROOT / path / "index.html" if path else WEB_ROOT / "index.html"


def main() -> int:
    sitemap = SITEMAP_SHARD.read_text(encoding="utf-8")
    failures = 0
    urls = [l.strip() for l in WHITELIST.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"whitelist urls: {len(urls)}")
    for u in urls:
        norm = u if u.endswith("/") else u + "/"
        f = file_for(norm)
        exists = f.is_file()
        noindexed = False
        if exists:
            noindexed = 'name="robots" content="noindex' in f.read_text(encoding="utf-8")
        in_sitemap = f"<loc>{norm}</loc>" in sitemap
        ok = exists and not noindexed and in_sitemap
        if not ok:
            failures += 1
        print(f"  [{'OK ' if ok else 'FAIL'}] exists={int(exists)} index={int(not noindexed)} sitemap={int(in_sitemap)}  {norm}")
    print(f"\nVERDICT: {'ALL OK — whitelist untouched' if failures == 0 else f'{failures} FAILURES'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
