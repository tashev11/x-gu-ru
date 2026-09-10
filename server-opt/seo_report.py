#!/usr/bin/env python3
"""Consolidated x-gu.ru SEO report for a programmatic page inventory.

Read-only report combining current release policy/inventory, full-corpus quality,
Yandex Webmaster and Google Search Console. Policy membership is evaluated for
the exact URL, including policy v2 city/service pairs.
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote


SERVER_OPT = Path(__file__).resolve().parent
REPO_ROOT = SERVER_OPT.parent
for path in (str(SERVER_OPT), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from programmatic_seo_audit import run_audit as run_programmatic_audit  # noqa: E402
from seo_healthcheck import (  # noqa: E402
    _default_keep_config,
    _default_whitelist,
    _expected_indexable,
    _load_index_policy,
    _normalize_base_url,
    _normalize_url,
    _page_url,
)


GSC_API_BASE = "https://www.googleapis.com/webmasters/v3"


def _backend_context():
    private_root = Path(os.getenv("XGU_PRIVATE_ROOT", "/opt/p3-app"))
    if str(private_root) not in sys.path:
        sys.path.insert(0, str(private_root))
    os.chdir(private_root)

    import requests  # noqa: PLC0415
    from app.core.config import settings  # noqa: PLC0415
    from app.services.google_search_console_service import _headers  # noqa: PLC0415
    from app.services.yandex_webmaster_service import _http_get, resolve_context  # noqa: PLC0415

    return requests, settings, _headers, _http_get, resolve_context


def _gsc_query(requests_module, headers_factory, site_url: str, days: int, dims: list[str], limit: int = 25000) -> list[dict]:
    endpoint = f"{GSC_API_BASE}/sites/{quote(site_url.strip(), safe='')}/searchAnalytics/query"
    end = date.today()
    start = end - timedelta(days=days)
    rows: list[dict] = []
    start_row = 0
    while True:
        body = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dimensions": dims,
            "rowLimit": limit,
            "startRow": start_row,
        }
        response = requests_module.post(endpoint, headers=headers_factory(), json=body, timeout=60)
        if not response.ok:
            raise RuntimeError(f"GSC {dims} failed: {response.status_code} {response.text[:300]}")
        batch = (response.json() or {}).get("rows", [])
        rows.extend(batch)
        if len(batch) < limit:
            break
        start_row += len(batch)
    return rows


def _yandex_in_search_urls(http_get, ctx) -> list[str]:
    urls: list[str] = []
    offset = 0
    while True:
        payload = http_get(
            f"/user/{ctx.user_id}/hosts/{ctx.host_id}/search-urls/in-search/samples"
            f"?offset={offset}&limit=100"
        )
        samples = payload.get("samples") or []
        for sample in samples:
            value = sample.get("url")
            if value:
                urls.append(str(value))
        count = payload.get("count")
        offset += len(samples)
        if not samples or (count is not None and offset >= int(count)):
            break
    return urls


def _release_policy(root: Path, base_url: str) -> dict:
    keep = _default_keep_config(root).resolve()
    whitelist = _default_whitelist(root, keep).resolve()
    policy = _load_index_policy(keep, whitelist, base_url)
    if policy is None:
        raise RuntimeError(f"release policy is not loadable: {keep}")
    return policy


def _policy_pair_count(policy: dict) -> int:
    if int(policy.get("policy_version") or 1) == 2:
        return len(policy.get("open_pairs") or [])
    return len(policy.get("open_cities") or []) * len(policy.get("open_services") or [])


def _current_page_urls(root: Path, base_url: str, policy: dict) -> tuple[set[str], set[str]]:
    open_urls: set[str] = set()
    closed_urls: set[str] = set()
    for html in root.rglob("index.html"):
        url = _normalize_url(_page_url(root, html, base_url))
        expected = _expected_indexable(url, base_url, policy)
        (open_urls if expected else closed_urls).add(url)
    return open_urls, closed_urls


def _print_programmatic_inventory(root: Path, base_url: str, policy: dict) -> None:
    audit = run_programmatic_audit(root, base_url=base_url)
    print("\n[Release] Programmatic SEO inventory:")
    print(f"  policy:                  v{policy.get('policy_version')} {policy.get('policy_mode')}")
    print(f"  policy open city hubs:   {len(policy.get('open_cities') or [])}")
    print(f"  policy service pairs:    {_policy_pair_count(policy)}")
    print(f"  physical pages:          {audit['physical_pages']}")
    print(f"  expected indexable:      {audit['indexable_pages']}")
    print(f"  policy-closed:           {audit['closed_pages']}")
    print(f"  indexable page types:    {audit['indexable_page_types']}")
    print(f"  thin indexable:          {audit['thin_indexable_pages']}")
    print(f"  orphan indexable:        {audit['orphan_indexable_pages']}")
    print(f"  open -> closed links:    {audit['indexable_links_to_closed']}")
    print(f"  exact duplicate pages:   {audit['exact_duplicate_pages']}")
    print(f"  near-duplicate pages:    {audit['near_duplicate_pages']}")


def _print_yandex(http_get, resolve_context, *, base_url: str, policy: dict) -> set[str]:
    ctx = resolve_context()
    print(f"\n[Яндекс] host: {ctx.host_display}")
    normalized: set[str] = set()

    try:
        summary = http_get(f"/user/{ctx.user_id}/hosts/{ctx.host_id}/summary")
        for key in ("sqi", "searchable_pages_count", "excluded_pages_count"):
            if key in summary:
                print(f"  {key}: {summary[key]}")
    except Exception as exc:  # noqa: BLE001
        print(f"  summary failed: {exc}")

    try:
        urls = _yandex_in_search_urls(http_get, ctx)
        normalized = {_normalize_url(url) for url in urls if url.startswith(base_url)}
        inside = sum(1 for url in normalized if _expected_indexable(url, base_url, policy))
        outside = len(normalized) - inside
        print(f"  in-search URLs sampled: {len(normalized)}")
        print(f"  inside exact release policy: {inside}")
        print(f"  OUTSIDE release policy:      {outside}")
        if outside:
            print("  URLs in Yandex but outside policy:")
            for url in sorted(url for url in normalized if not _expected_indexable(url, base_url, policy))[:30]:
                print(f"    {url}")
    except Exception as exc:  # noqa: BLE001
        print(f"  in-search URLs failed: {exc}")

    try:
        query_payload = http_get(
            f"/user/{ctx.user_id}/hosts/{ctx.host_id}/search-queries/popular"
            "?order_by=TOTAL_SHOWS&query_indicator=TOTAL_SHOWS"
            "&query_indicator=TOTAL_CLICKS&query_indicator=AVG_SHOW_POSITION"
        )
        rows = query_payload.get("queries") or []
        print(f"  top queries returned: {len(rows)}")
        for row in rows[:12]:
            indicators = row.get("indicators", {})
            print(
                f"    shows={indicators.get('TOTAL_SHOWS',0):>5} "
                f"clicks={indicators.get('TOTAL_CLICKS',0):>3} "
                f"pos={indicators.get('AVG_SHOW_POSITION',0):>5} | {row.get('query_text','')}"
            )
    except Exception as exc:  # noqa: BLE001
        print(f"  popular queries failed: {exc}")

    return normalized


def _print_google(
    requests_module,
    headers_factory,
    site_url: str,
    *,
    base_url: str,
    policy: dict,
    open_urls: set[str],
    days: int,
) -> set[str]:
    pages = _gsc_query(requests_module, headers_factory, site_url, days, ["page"])
    pages.sort(key=lambda row: -float(row.get("impressions") or 0))

    normalized_signal_urls: set[str] = set()
    outside_rows: list[dict] = []
    for row in pages:
        keys = row.get("keys") or []
        if not keys:
            continue
        url = _normalize_url(str(keys[0]))
        if not url.startswith(base_url):
            continue
        normalized_signal_urls.add(url)
        if not _expected_indexable(url, base_url, policy):
            outside_rows.append(row)

    print(f"\n[Google] page signals, last {days}d:")
    print(f"  pages with impressions/click data: {len(normalized_signal_urls)}")
    print(f"  inside exact release policy:       {len(normalized_signal_urls) - len(outside_rows)}")
    print(f"  OUTSIDE release policy:            {len(outside_rows)}")

    if outside_rows:
        print("  Highest-impression Google URLs outside policy:")
        for row in outside_rows[:30]:
            print(
                f"    imp={row.get('impressions',0):>7.0f} "
                f"clicks={row.get('clicks',0):>5.0f} "
                f"pos={row.get('position',0):>6.1f}  {row['keys'][0]}"
            )

    open_without_gsc = sorted(open_urls - normalized_signal_urls)
    print(f"  open release pages with ZERO GSC page signal in window: {len(open_without_gsc)}")
    if open_without_gsc:
        print("  examples:")
        for url in open_without_gsc[:20]:
            print(f"    {url}")

    print("  top pages:")
    for row in pages[:10]:
        print(
            f"    imp={row.get('impressions',0):>7.0f} "
            f"clicks={row.get('clicks',0):>5.0f} "
            f"pos={row.get('position',0):>6.1f}  {row['keys'][0]}"
        )

    queries = _gsc_query(requests_module, headers_factory, site_url, days, ["query"])
    queries.sort(key=lambda row: -float(row.get("impressions") or 0))
    print(f"  query rows: {len(queries)}")
    for row in queries[:12]:
        print(
            f"    imp={row.get('impressions',0):>7.0f} "
            f"clicks={row.get('clicks',0):>5.0f} "
            f"pos={row.get('position',0):>6.1f}  {row['keys'][0]}"
        )
    return normalized_signal_urls


def main() -> int:
    root = Path(os.getenv("SEOHC_ROOT", "/var/www/x-gu.ru/current")).resolve()
    base_url = _normalize_base_url(os.getenv("SEOHC_BASE_URL", "https://x-gu.ru"))
    days = int(os.getenv("SEO_REPORT_DAYS", "90"))

    if not root.is_dir():
        print(f"Release root not found: {root}", file=sys.stderr)
        return 2
    if days < 1:
        print("SEO_REPORT_DAYS must be positive", file=sys.stderr)
        return 2

    try:
        policy = _release_policy(root, base_url)
        open_urls, closed_urls = _current_page_urls(root, base_url, policy)
        _print_programmatic_inventory(root, base_url, policy)
        print(f"  exact policy open URLs on disk:   {len(open_urls)}")
        print(f"  exact policy closed URLs on disk: {len(closed_urls)}")

        requests_module, settings, headers_factory, http_get, resolve_context = _backend_context()
        yandex_urls = _print_yandex(http_get, resolve_context, base_url=base_url, policy=policy)
        google_urls = _print_google(
            requests_module,
            headers_factory,
            settings.google_search_console_site_url,
            base_url=base_url,
            policy=policy,
            open_urls=open_urls,
            days=days,
        )

        combined = yandex_urls | google_urls
        protected_outside = sorted(url for url in combined if not _expected_indexable(url, base_url, policy))
        print("\n[Cross-engine] evidence vs release policy:")
        print(f"  unique URLs with Yandex/GSC evidence: {len(combined)}")
        print(f"  evidence URLs outside current policy: {len(protected_outside)}")
        for url in protected_outside[:30]:
            print(f"    REVIEW {url}")
    except Exception as exc:  # noqa: BLE001
        print(f"SEO report failed: {exc}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
