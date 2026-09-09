#!/usr/bin/env python3
"""Critical check after the index shrink:
did we noindex any page that GOOGLE was already showing/ranking?

The whitelist was built from Yandex only. Google may have had a different
(possibly larger) set of pages with impressions. This queries GSC Search
Analytics for every page with impressions in the last 3 months and reports
which of them are now noindexed.

Output: /opt/p3-app/data/gsc_pages.json  (raw rows)
        console report: at-risk URLs (had impressions, now noindex)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote, urlparse

sys.path.insert(0, "/opt/p3-app")
os.chdir("/opt/p3-app")

import requests  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.services.google_search_console_service import _headers  # noqa: E402

GSC_API_BASE = "https://www.googleapis.com/webmasters/v3"
WEB_ROOT = Path("/var/www/x-gu.ru/current")
OUT = Path("/opt/p3-app/data/gsc_pages.json")


def query_pages(days: int = 90, row_limit: int = 25000) -> list[dict]:
    site = settings.google_search_console_site_url.strip()
    url = f"{GSC_API_BASE}/sites/{quote(site, safe='')}/searchAnalytics/query"
    end = date.today()
    start = end - timedelta(days=days)
    body = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "dimensions": ["page"],
        "rowLimit": row_limit,
    }
    resp = requests.post(url, headers=_headers(), json=body, timeout=60)
    if not resp.ok:
        raise SystemExit(f"GSC query failed: {resp.status_code} {resp.text[:500]}")
    return (resp.json() or {}).get("rows", [])


def file_for(url: str) -> Path:
    path = urlparse(url).path.strip("/")
    return WEB_ROOT / path / "index.html" if path else WEB_ROOT / "index.html"


def is_noindexed(url: str) -> bool | None:
    f = file_for(url)
    if not f.is_file():
        return None  # page doesn't exist
    return 'name="robots" content="noindex' in f.read_text(encoding="utf-8", errors="ignore")


def main() -> int:
    rows = query_pages()
    print(f"GSC pages with data (90d): {len(rows)}")
    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    at_risk: list[tuple[str, float, float, float]] = []
    ok_open = 0
    missing = 0
    for r in rows:
        page = r["keys"][0]
        imps = r.get("impressions", 0)
        clicks = r.get("clicks", 0)
        pos = r.get("position", 0)
        state = is_noindexed(page)
        if state is None:
            missing += 1
        elif state:
            at_risk.append((page, imps, clicks, pos))
        else:
            ok_open += 1

    at_risk.sort(key=lambda x: -x[1])
    print(f"\nstill open: {ok_open} | file missing: {missing} | NOW NOINDEXED: {len(at_risk)}")
    if at_risk:
        print("\n=== pages Google had data for, but we closed ===")
        print(f"{'impr':>6} {'clicks':>6} {'pos':>6}  url")
        for page, imps, clicks, pos in at_risk:
            print(f"{imps:>6.0f} {clicks:>6.0f} {pos:>6.1f}  {page}")
        total_imps = sum(x[1] for x in at_risk)
        total_clicks = sum(x[2] for x in at_risk)
        print(f"\nlost exposure: {total_imps:.0f} impressions, {total_clicks:.0f} clicks (90d)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
