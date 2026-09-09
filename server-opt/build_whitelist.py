#!/usr/bin/env python3
"""Build whitelist.txt: pages currently in Yandex search (in-search samples)
plus popular queries data for cross-reference.

Uses the existing yandex_webmaster_service auth/context helpers.
Output: /opt/p3-app/data/whitelist.txt (one URL per line)
        /opt/p3-app/data/whitelist_queries.json (queries with shows, reference)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, "/opt/p3-app")
import os
os.chdir("/opt/p3-app")

from app.services.yandex_webmaster_service import (  # noqa: E402
    _http_get,
    resolve_context,
)

OUT_URLS = Path("/opt/p3-app/data/whitelist.txt")
OUT_QUERIES = Path("/opt/p3-app/data/whitelist_queries.json")


def fetch_in_search_urls(ctx) -> list[str]:
    """Page through in-search samples (API caps limit at 100)."""
    urls: list[str] = []
    offset = 0
    while True:
        payload = _http_get(
            f"/user/{ctx.user_id}/hosts/{ctx.host_id}/search-urls/in-search/samples"
            f"?offset={offset}&limit=100"
        )
        samples = payload.get("samples") or []
        for s in samples:
            u = s.get("url")
            if u:
                urls.append(u)
        count = payload.get("count")
        offset += len(samples)
        if not samples or (count is not None and offset >= int(count)):
            break
    return urls


def fetch_popular_queries(ctx) -> list[dict]:
    """Popular queries with shows/clicks (reference only — API has no per-URL
    breakdown, but lets us sanity-check that ranked queries' pages are inside
    the in-search set)."""
    try:
        payload = _http_get(
            f"/user/{ctx.user_id}/hosts/{ctx.host_id}/search-queries/popular"
            f"?order_by=TOTAL_SHOWS&query_indicator=TOTAL_SHOWS&query_indicator=TOTAL_CLICKS&query_indicator=AVG_SHOW_POSITION"
        )
        return payload.get("queries") or []
    except Exception as e:  # noqa: BLE001
        print(f"popular queries fetch failed (non-fatal): {e}")
        return []


def main() -> int:
    ctx = resolve_context()
    print(f"host: {ctx.host_display} ({ctx.host_id})")

    urls = fetch_in_search_urls(ctx)
    print(f"in-search urls: {len(urls)}")
    for u in urls:
        print(f"  {u}")

    OUT_URLS.write_text("\n".join(sorted(set(urls))) + "\n", encoding="utf-8")
    print(f"written: {OUT_URLS} ({len(set(urls))} unique)")

    queries = fetch_popular_queries(ctx)
    OUT_QUERIES.write_text(json.dumps(queries, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"queries saved: {len(queries)} -> {OUT_QUERIES}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
