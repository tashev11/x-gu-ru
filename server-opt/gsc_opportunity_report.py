#!/usr/bin/env python3
"""Rank x-gu.ru SEO opportunities from GSC query+page data.

The report is read-only. It highlights high-impression low-CTR snippets,
striking-distance pages and closed URLs still receiving Google search signal.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Callable


SERVER_OPT = Path(__file__).resolve().parent
REPO_ROOT = SERVER_OPT.parent
for path in (str(SERVER_OPT), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from gsc_cannibalization_report import canonical_page, fetch_query_page_rows, page_kind  # noqa: E402
from seo_healthcheck import (  # noqa: E402
    _default_keep_config,
    _default_whitelist,
    _expected_indexable,
    _load_index_policy,
    _normalize_base_url,
)


DEFAULT_ROOT = Path("/var/www/x-gu.ru/current")
DEFAULT_OUT = Path("/opt/p3-app/data/gsc_opportunities.json")

CATEGORY_PRIORITY = {
    "CLOSED_SIGNAL_REVIEW": 100,
    "SNIPPET_REVIEW": 80,
    "STRIKING_DISTANCE": 70,
    "CONTENT_GROWTH": 50,
    "STRONG_PAGE": 20,
    "LOW_PRIORITY": 10,
}


def analyze_rows(
    rows: list[dict],
    *,
    policy_resolver: Callable[[str], bool | None] | None = None,
    min_impressions: float = 20.0,
    max_snippet_ctr: float = 0.02,
    snippet_max_position: float = 10.0,
    striking_min_position: float = 8.0,
    striking_max_position: float = 20.0,
    content_max_position: float = 40.0,
    top_queries: int = 10,
) -> dict:
    if min_impressions < 0 or max_snippet_ctr < 0:
        raise ValueError("impression/CTR thresholds must be non-negative")
    if not 0 < snippet_max_position <= striking_max_position <= content_max_position:
        raise ValueError("position thresholds must be positive and ordered")
    if not 0 < striking_min_position <= striking_max_position:
        raise ValueError("striking position range is invalid")
    if top_queries < 1:
        raise ValueError("top_queries must be positive")

    pages: dict[str, dict] = defaultdict(
        lambda: {
            "impressions": 0.0,
            "clicks": 0.0,
            "position_weight": 0.0,
            "queries": defaultdict(lambda: {"impressions": 0.0, "clicks": 0.0, "position_weight": 0.0}),
        }
    )

    for row in rows:
        keys = row.get("keys") or []
        if len(keys) < 2:
            continue
        query = str(keys[0]).strip()
        page = canonical_page(str(keys[1]))
        impressions = float(row.get("impressions") or 0.0)
        if not query or page is None or impressions <= 0:
            continue
        clicks = float(row.get("clicks") or 0.0)
        position = float(row.get("position") or 0.0)
        item = pages[page]
        item["impressions"] += impressions
        item["clicks"] += clicks
        item["position_weight"] += position * impressions
        q = item["queries"][query]
        q["impressions"] += impressions
        q["clicks"] += clicks
        q["position_weight"] += position * impressions

    opportunities: list[dict] = []
    category_counts: dict[str, int] = defaultdict(int)
    for url, metrics in pages.items():
        impressions = metrics["impressions"]
        if impressions < min_impressions:
            continue
        clicks = metrics["clicks"]
        avg_position = metrics["position_weight"] / impressions if impressions else 0.0
        ctr = clicks / impressions if impressions else 0.0
        policy_open = policy_resolver(url) if policy_resolver is not None else None

        if policy_open is False:
            category = "CLOSED_SIGNAL_REVIEW"
            score = impressions
        elif avg_position <= snippet_max_position and ctr <= max_snippet_ctr:
            category = "SNIPPET_REVIEW"
            score = impressions * max(0.0, max_snippet_ctr - ctr + 0.001) * 100.0
        elif avg_position < striking_min_position:
            category = "STRONG_PAGE"
            score = impressions * max(ctr, 0.001)
        elif striking_min_position <= avg_position <= striking_max_position:
            category = "STRIKING_DISTANCE"
            score = impressions * max(0.1, (striking_max_position + 1.0 - avg_position) / striking_max_position)
        elif avg_position <= content_max_position:
            category = "CONTENT_GROWTH"
            score = impressions * max(0.05, (content_max_position + 1.0 - avg_position) / content_max_position)
        else:
            category = "LOW_PRIORITY"
            score = impressions / max(avg_position, 1.0)

        query_rows = []
        for query, qmetrics in metrics["queries"].items():
            qimp = qmetrics["impressions"]
            query_rows.append(
                {
                    "query": query,
                    "impressions": qimp,
                    "clicks": qmetrics["clicks"],
                    "ctr": round(qmetrics["clicks"] / qimp, 5) if qimp else 0.0,
                    "position": round(qmetrics["position_weight"] / qimp, 2) if qimp else None,
                }
            )
        query_rows.sort(key=lambda item: (-item["impressions"], -item["clicks"], item["query"]))

        opportunities.append(
            {
                "url": url,
                "kind": page_kind(url),
                "policy_open": policy_open,
                "category": category,
                "priority": CATEGORY_PRIORITY[category],
                "opportunity_score": round(score, 3),
                "impressions": impressions,
                "clicks": clicks,
                "ctr": round(ctr, 5),
                "position": round(avg_position, 2),
                "top_queries": query_rows[:top_queries],
            }
        )
        category_counts[category] += 1

    opportunities.sort(
        key=lambda item: (
            -item["priority"],
            -item["opportunity_score"],
            -item["impressions"],
            item["url"],
        )
    )
    return {
        "input_rows": len(rows),
        "pages_meeting_impression_threshold": len(opportunities),
        "thresholds": {
            "min_impressions": min_impressions,
            "max_snippet_ctr": max_snippet_ctr,
            "snippet_max_position": snippet_max_position,
            "striking_min_position": striking_min_position,
            "striking_max_position": striking_max_position,
            "content_max_position": content_max_position,
        },
        "category_counts": dict(sorted(category_counts.items())),
        "opportunities": opportunities,
        "automatic_changes": False,
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


def _policy_resolver(root: Path, base_url: str) -> Callable[[str], bool | None]:
    keep = _default_keep_config(root).resolve()
    whitelist = _default_whitelist(root, keep).resolve()
    policy = _load_index_policy(keep, whitelist, base_url)
    if policy is None:
        raise RuntimeError("release policy could not be loaded")
    return lambda url: _expected_indexable(url, base_url, policy)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--min-impressions", type=float, default=20.0)
    parser.add_argument("--max-snippet-ctr", type=float, default=0.02)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    if not args.root.is_dir():
        print(f"release root not found: {args.root}", file=sys.stderr)
        return 2
    if args.days < 1:
        print("days must be positive", file=sys.stderr)
        return 2
    try:
        requests_module, settings, headers_factory = _backend_context()
        rows = fetch_query_page_rows(
            requests_module,
            headers_factory,
            settings.google_search_console_site_url,
            days=args.days,
        )
        base_url = _normalize_base_url("https://x-gu.ru")
        report = analyze_rows(
            rows,
            policy_resolver=_policy_resolver(args.root, base_url),
            min_impressions=args.min_impressions,
            max_snippet_ctr=args.max_snippet_ctr,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"GSC opportunity report failed: {exc}", file=sys.stderr)
        return 2

    report["window_days"] = args.days
    print("GSC SEO opportunity report")
    for category, count in report["category_counts"].items():
        print(f"  {category:22} {count}")
    for item in report["opportunities"][:20]:
        print(
            f"  {item['category']:22} imp={item['impressions']:>7.0f} "
            f"ctr={item['ctr']:.3f} pos={item['position']:>5} {item['url']}"
        )

    if not args.out.parent.is_dir():
        print(f"output parent not found: {args.out.parent}", file=sys.stderr)
        return 3
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
