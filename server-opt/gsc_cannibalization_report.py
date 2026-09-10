#!/usr/bin/env python3
"""Find x-gu.ru query/page cannibalization from Google Search Console.

The report groups Search Analytics rows by query and highlights queries where
multiple x-gu.ru URLs receive impressions. Same-city competing service pages are
reported separately because they are especially useful for programmatic SEO
consolidation decisions.

Search Console may return only top rows under its internal data limits, so this
is a diagnostic signal rather than a mathematically complete crawl/search log.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote, urlparse


GSC_API_BASE = "https://www.googleapis.com/webmasters/v3"
DEFAULT_OUT = Path("/opt/p3-app/data/gsc_cannibalization.json")
BASE_HOST = "x-gu.ru"


def canonical_page(value: str) -> str | None:
    parsed = urlparse(str(value).strip())
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {BASE_HOST, f"www.{BASE_HOST}"}:
        return None
    path = parsed.path or "/"
    if path != "/" and not path.endswith("/") and "." not in Path(path).name:
        path += "/"
    return f"https://{BASE_HOST}{path}"


def page_city(url: str) -> str:
    parts = [part for part in urlparse(url).path.split("/") if part]
    return parts[0] if parts else ""


def page_kind(url: str) -> str:
    parts = [part for part in urlparse(url).path.split("/") if part]
    if not parts:
        return "home"
    if len(parts) == 1:
        return "city_hub"
    if len(parts) == 2:
        return "service_landing"
    return "other"


def fetch_query_page_rows(
    requests_module,
    headers_factory,
    site_url: str,
    *,
    days: int = 90,
    row_limit: int = 25000,
    max_rows: int = 50000,
) -> list[dict]:
    if days < 1:
        raise ValueError("days must be positive")
    if not 1 <= row_limit <= 25000:
        raise ValueError("row_limit must be between 1 and 25000")
    if max_rows < 1:
        raise ValueError("max_rows must be positive")

    end = date.today()
    start = end - timedelta(days=days)
    endpoint = f"{GSC_API_BASE}/sites/{quote(site_url.strip(), safe='')}/searchAnalytics/query"
    rows: list[dict] = []
    start_row = 0
    while len(rows) < max_rows:
        limit = min(row_limit, max_rows - len(rows))
        body = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dimensions": ["query", "page"],
            "rowLimit": limit,
            "startRow": start_row,
        }
        response = requests_module.post(endpoint, headers=headers_factory(), json=body, timeout=60)
        if not response.ok:
            raise RuntimeError(f"GSC query/page request failed: {response.status_code} {response.text[:300]}")
        batch = (response.json() or {}).get("rows", [])
        rows.extend(batch)
        if len(batch) < limit:
            break
        start_row += len(batch)
    return rows


def analyze_rows(
    rows: list[dict],
    *,
    min_row_impressions: float = 1.0,
    min_pages_per_query: int = 2,
    max_pages_per_query: int = 20,
) -> dict:
    if min_row_impressions < 0:
        raise ValueError("min_row_impressions must be non-negative")
    if min_pages_per_query < 2:
        raise ValueError("min_pages_per_query must be at least 2")
    if max_pages_per_query < min_pages_per_query:
        raise ValueError("max_pages_per_query must be >= min_pages_per_query")

    grouped: dict[str, dict[str, dict[str, float]]] = defaultdict(
        lambda: defaultdict(lambda: {"impressions": 0.0, "clicks": 0.0, "position_weight": 0.0})
    )
    display_query: dict[str, str] = {}

    for row in rows:
        keys = row.get("keys") or []
        if len(keys) < 2:
            continue
        query = str(keys[0]).strip()
        page = canonical_page(str(keys[1]))
        impressions = float(row.get("impressions") or 0.0)
        if not query or page is None or impressions < min_row_impressions:
            continue
        key = " ".join(query.casefold().split())
        display_query.setdefault(key, query)
        item = grouped[key][page]
        item["impressions"] += impressions
        item["clicks"] += float(row.get("clicks") or 0.0)
        item["position_weight"] += float(row.get("position") or 0.0) * impressions

    query_conflicts: list[dict] = []
    pair_stats: dict[tuple[str, str], dict] = {}
    same_city_pairs: dict[tuple[str, str], dict] = {}

    for key, page_map in grouped.items():
        if len(page_map) < min_pages_per_query:
            continue
        pages = []
        for page, metrics in page_map.items():
            impressions = metrics["impressions"]
            pages.append(
                {
                    "url": page,
                    "city": page_city(page),
                    "kind": page_kind(page),
                    "impressions": impressions,
                    "clicks": metrics["clicks"],
                    "position": (metrics["position_weight"] / impressions) if impressions else 0.0,
                }
            )
        pages.sort(key=lambda item: (-item["impressions"], -item["clicks"], item["url"]))
        pages = pages[:max_pages_per_query]
        if len(pages) < min_pages_per_query:
            continue

        total_impressions = sum(item["impressions"] for item in pages)
        total_clicks = sum(item["clicks"] for item in pages)
        same_city = len({item["city"] for item in pages if item["city"]}) == 1
        query_conflicts.append(
            {
                "query": display_query[key],
                "pages": pages,
                "page_count": len(pages),
                "total_impressions": total_impressions,
                "total_clicks": total_clicks,
                "same_city": same_city,
            }
        )

        for left, right in itertools.combinations(pages, 2):
            pair = tuple(sorted((left["url"], right["url"])))
            target = pair_stats.setdefault(
                pair,
                {
                    "pages": list(pair),
                    "shared_queries": 0,
                    "shared_impressions": 0.0,
                    "queries": [],
                },
            )
            target["shared_queries"] += 1
            target["shared_impressions"] += left["impressions"] + right["impressions"]
            target["queries"].append(display_query[key])

            if left["city"] and left["city"] == right["city"]:
                local = same_city_pairs.setdefault(
                    pair,
                    {
                        "city": left["city"],
                        "pages": list(pair),
                        "shared_queries": 0,
                        "shared_impressions": 0.0,
                        "queries": [],
                    },
                )
                local["shared_queries"] += 1
                local["shared_impressions"] += left["impressions"] + right["impressions"]
                local["queries"].append(display_query[key])

    query_conflicts.sort(key=lambda item: (-item["total_impressions"], -item["page_count"], item["query"]))
    competing_pairs = sorted(
        pair_stats.values(),
        key=lambda item: (-item["shared_queries"], -item["shared_impressions"], item["pages"]),
    )
    local_pairs = sorted(
        same_city_pairs.values(),
        key=lambda item: (-item["shared_queries"], -item["shared_impressions"], item["pages"]),
    )
    for item in competing_pairs + local_pairs:
        item["queries"] = sorted(set(item["queries"]))[:50]

    affected_pages = {page["url"] for conflict in query_conflicts for page in conflict["pages"]}
    return {
        "input_rows": len(rows),
        "queries_with_multiple_pages": len(query_conflicts),
        "affected_pages": len(affected_pages),
        "competing_page_pairs": len(competing_pairs),
        "same_city_competing_pairs": len(local_pairs),
        "query_conflicts": query_conflicts[:500],
        "competing_pairs": competing_pairs[:500],
        "same_city_pairs": local_pairs[:500],
    }


def _backend_context():
    private_root = Path(os.getenv("XGU_PRIVATE_ROOT", "/opt/p3-app"))
    if str(private_root) not in sys.path:
        sys.path.insert(0, str(private_root))
    os.chdir(private_root)

    import requests  # noqa: PLC0415
    from app.core.config import settings  # noqa: PLC0415
    from app.services.google_search_console_service import _headers  # noqa: PLC0415

    return requests, settings, _headers


def _atomic_write(path: Path, payload: dict) -> None:
    if not path.parent.is_dir():
        raise FileNotFoundError(f"output parent does not exist: {path.parent}")
    if path.is_symlink() or path.parent.is_symlink():
        raise RuntimeError(f"refusing symlink output path: {path}")
    temp = path.with_name(f".{path.name}.next.{os.getpid()}")
    try:
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--row-limit", type=int, default=25000)
    parser.add_argument("--max-rows", type=int, default=50000)
    parser.add_argument("--min-row-impressions", type=float, default=1.0)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    try:
        requests_module, settings, headers_factory = _backend_context()
        rows = fetch_query_page_rows(
            requests_module,
            headers_factory,
            settings.google_search_console_site_url,
            days=args.days,
            row_limit=args.row_limit,
            max_rows=args.max_rows,
        )
        report = analyze_rows(rows, min_row_impressions=args.min_row_impressions)
    except Exception as exc:  # noqa: BLE001
        print(f"GSC cannibalization audit failed: {exc}", file=sys.stderr)
        return 2

    report["window_days"] = args.days
    report["data_limit_note"] = (
        "Search Console Search Analytics is bounded by Google's internal/top-row data limits; "
        "absence from this report is not proof that a query/page relationship never occurred."
    )

    print("GSC cannibalization report")
    print(f"  query/page rows:             {report['input_rows']}")
    print(f"  queries with >1 page:        {report['queries_with_multiple_pages']}")
    print(f"  affected pages:              {report['affected_pages']}")
    print(f"  competing page pairs:        {report['competing_page_pairs']}")
    print(f"  SAME-CITY competing pairs:   {report['same_city_competing_pairs']}")
    for item in report["same_city_pairs"][:15]:
        print(
            f"  REVIEW {item['city']}: shared_queries={item['shared_queries']} "
            f"shared_impressions={item['shared_impressions']:.0f} | {' <> '.join(item['pages'])}"
        )

    try:
        _atomic_write(args.out, report)
    except Exception as exc:  # noqa: BLE001
        print(f"Cannot write report: {exc}", file=sys.stderr)
        return 3
    print(f"  report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
