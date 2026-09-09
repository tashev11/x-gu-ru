#!/usr/bin/env python3
"""Consolidated SEO status report: Yandex Webmaster + Google Search Console.

Compares the current state against the pre-shrink baseline (2026-08-14):
sitemap 37,625 -> 1,097 URLs; Yandex in-search was 41.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, "/opt/p3-app")
os.chdir("/opt/p3-app")

import requests  # noqa: E402
from app.core.config import settings  # noqa: E402

# ---------------- Yandex ----------------
from app.services.yandex_webmaster_service import (  # noqa: E402
    _http_get, resolve_context,
)


def yandex_block() -> None:
    ctx = resolve_context()
    print(f"host: {ctx.host_display}")

    # indexing summary
    try:
        s = _http_get(f"/user/{ctx.user_id}/hosts/{ctx.host_id}/summary")
        print("\n[Яндекс] Сводка:")
        for k in ("sqi", "searchable_pages_count", "excluded_pages_count",
                  "site_problems"):
            if k in s:
                print(f"  {k}: {s[k]}")
    except Exception as e:  # noqa: BLE001
        print(f"  summary failed: {e}")

    # in-search count history
    try:
        h = _http_get(
            f"/user/{ctx.user_id}/hosts/{ctx.host_id}/search-urls/in-search/history")
        pts = h.get("history", [])[-8:]
        print("\n[Яндекс] Страниц в поиске (динамика):")
        for p in pts:
            print(f"  {p.get('date','')[:10]}  {p.get('value')}")
    except Exception as e:  # noqa: BLE001
        print(f"  in-search history failed: {e}")

    # indexing history (crawled)
    try:
        end = date.today()
        start = end - timedelta(days=21)
        ih = _http_get(
            f"/user/{ctx.user_id}/hosts/{ctx.host_id}/indexing/history"
            f"?date_from={start.isoformat()}&date_to={end.isoformat()}")
        pts = ih.get("indicators", {}).get("SEARCHABLE", [])[-6:]
        if pts:
            print("\n[Яндекс] Индексация (SEARCHABLE, 21д):")
            for p in pts:
                print(f"  {p.get('date','')[:10]}  {p.get('value')}")
    except Exception as e:  # noqa: BLE001
        print(f"  indexing history failed: {e}")

    # top queries
    try:
        q = _http_get(
            f"/user/{ctx.user_id}/hosts/{ctx.host_id}/search-queries/popular"
            f"?order_by=TOTAL_SHOWS&query_indicator=TOTAL_SHOWS"
            f"&query_indicator=TOTAL_CLICKS&query_indicator=AVG_SHOW_POSITION")
        rows = q.get("queries", [])
        print(f"\n[Яндекс] Топ-запросы ({len(rows)}):")
        for r in rows[:15]:
            ind = r.get("indicators", {})
            print(f"  показы={ind.get('TOTAL_SHOWS',0):>5} клики={ind.get('TOTAL_CLICKS',0):>3} "
                  f"поз={ind.get('AVG_SHOW_POSITION',0):>5} | {r.get('query_text','')}")
    except Exception as e:  # noqa: BLE001
        print(f"  queries failed: {e}")


# ---------------- Google ----------------
from app.services.google_search_console_service import _headers  # noqa: E402

GSC = "https://www.googleapis.com/webmasters/v3"


def gsc_query(days: int, dims: list[str], limit: int = 25000) -> list[dict]:
    site = settings.google_search_console_site_url.strip()
    url = f"{GSC}/sites/{quote(site, safe='')}/searchAnalytics/query"
    end = date.today()
    start = end - timedelta(days=days)
    body = {"startDate": start.isoformat(), "endDate": end.isoformat(),
            "dimensions": dims, "rowLimit": limit}
    r = requests.post(url, headers=_headers(), json=body, timeout=60)
    if not r.ok:
        print(f"  GSC {dims} failed: {r.status_code} {r.text[:200]}")
        return []
    return (r.json() or {}).get("rows", [])


def google_block() -> None:
    for window in (14, 28):
        rows = gsc_query(window, ["date"])
        if not rows:
            continue
        imps = sum(r.get("impressions", 0) for r in rows)
        clicks = sum(r.get("clicks", 0) for r in rows)
        print(f"\n[Google] Последние {window} дней: показы={imps:.0f} клики={clicks:.0f} "
              f"дней с данными={len(rows)}")

    pages = gsc_query(28, ["page"])
    print(f"\n[Google] Страниц с показами за 28д: {len(pages)}")
    pages.sort(key=lambda r: -r.get("impressions", 0))
    print("  топ-10 по показам:")
    for r in pages[:10]:
        print(f"    imp={r.get('impressions',0):>5.0f} clicks={r.get('clicks',0):>3.0f} "
              f"pos={r.get('position',0):>5.1f}  {r['keys'][0]}")

    qs = gsc_query(28, ["query"])
    print(f"\n[Google] Запросов за 28д: {len(qs)}")
    qs.sort(key=lambda r: -r.get("impressions", 0))
    for r in qs[:12]:
        print(f"    imp={r.get('impressions',0):>5.0f} clicks={r.get('clicks',0):>3.0f} "
              f"pos={r.get('position',0):>5.1f}  {r['keys'][0]}")

    # how many of the pages with impressions are inside our open core?
    keep = json.loads(Path("/opt/p3-app/data/index_keep_config.json").read_text(encoding="utf-8"))
    open_c = set(keep["open_cities"])
    inside = outside = 0
    for r in pages:
        u = r["keys"][0]
        parts = [p for p in u.split("x-gu.ru", 1)[-1].split("/") if p]
        city = parts[0] if parts else ""
        if not parts or city in open_c:
            inside += 1
        else:
            outside += 1
    print(f"\n[Google] Страницы с показами: в открытом ядре={inside}, вне ядра={outside}")


if __name__ == "__main__":
    yandex_block()
    print("\n" + "=" * 60)
    google_block()
