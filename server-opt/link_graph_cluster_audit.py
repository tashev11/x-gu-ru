#!/usr/bin/env python3
"""Audit crawl depth and same-service template similarity across x-gu.ru pages."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path
from urllib.parse import urlparse


SERVER_OPT = Path(__file__).resolve().parent
REPO_ROOT = SERVER_OPT.parent
for path in (str(SERVER_OPT), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from programmatic_seo_audit import (  # noqa: E402
    _near_duplicate_groups,
    _normalized_body,
    _simhash64,
    collect_pages,
)
from seo_healthcheck import _internal_page_target, _normalize_base_url  # noqa: E402


def _service_slug(url: str) -> str | None:
    parts = [part for part in urlparse(url).path.split("/") if part]
    return parts[1] if len(parts) == 2 else None


def run_audit(
    root: Path,
    *,
    base_url: str = "https://x-gu.ru",
    deep_threshold: int = 3,
    max_simhash_distance: int = 6,
) -> dict:
    if deep_threshold < 1:
        raise ValueError("deep_threshold must be positive")
    base_url = _normalize_base_url(base_url)
    records, _policy = collect_pages(root, base_url)
    indexable = [record for record in records if record.indexable]
    by_url = {record.url: record for record in indexable}
    indexable_urls = set(by_url)

    adjacency: dict[str, set[str]] = {url: set() for url in indexable_urls}
    for record in indexable:
        for href in record.hrefs:
            target = _internal_page_target(href, record.url, base_url)
            if target in indexable_urls and target != record.url:
                adjacency[record.url].add(target)

    home = base_url + "/"
    depths: dict[str, int] = {}
    if home in indexable_urls:
        depths[home] = 0
        queue: deque[str] = deque([home])
        while queue:
            source = queue.popleft()
            next_depth = depths[source] + 1
            for target in sorted(adjacency[source]):
                if target not in depths:
                    depths[target] = next_depth
                    queue.append(target)

    unreachable = sorted(indexable_urls - set(depths))
    deep_urls = sorted(url for url, depth in depths.items() if depth > deep_threshold)
    distribution = Counter(depths.values())

    service_hashes: dict[str, dict[str, int]] = defaultdict(dict)
    service_exact: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for record in indexable:
        service = _service_slug(record.url)
        if service is None:
            continue
        normalized = _normalized_body(record.body)
        service_hashes[service][record.url] = _simhash64(normalized)
        service_exact[service][normalized].append(record.url)

    service_clusters: list[dict] = []
    same_service_near_pages: set[str] = set()
    same_service_exact_pages: set[str] = set()
    for service, hashes in sorted(service_hashes.items()):
        if len(hashes) < 2:
            continue
        near_groups = _near_duplicate_groups(hashes, max_simhash_distance)
        exact_groups = [sorted(group) for group in service_exact[service].values() if len(group) > 1]
        exact_groups.sort(key=lambda group: (-len(group), group[0]))
        if not near_groups and not exact_groups:
            continue
        near_pages = {url for group in near_groups for url in group}
        exact_pages = {url for group in exact_groups for url in group}
        same_service_near_pages.update(near_pages)
        same_service_exact_pages.update(exact_pages)
        service_clusters.append(
            {
                "service": service,
                "indexable_pages": len(hashes),
                "near_duplicate_pages": len(near_pages),
                "near_duplicate_groups": near_groups[:20],
                "exact_duplicate_pages": len(exact_pages),
                "exact_duplicate_groups": exact_groups[:20],
            }
        )

    service_clusters.sort(
        key=lambda item: (-item["near_duplicate_pages"], -item["exact_duplicate_pages"], item["service"])
    )

    return {
        "root": str(root.resolve()),
        "base_url": base_url,
        "indexable_pages": len(indexable),
        "home_present": home in indexable_urls,
        "reachable_from_home": len(depths),
        "unreachable_from_home_pages": len(unreachable),
        "unreachable_from_home_urls": unreachable,
        "max_crawl_depth": max(depths.values(), default=None),
        "crawl_depth_distribution": {str(depth): count for depth, count in sorted(distribution.items())},
        "deep_threshold": deep_threshold,
        "deep_indexable_pages": len(deep_urls),
        "deep_indexable_urls": deep_urls,
        "same_service_near_duplicate_pages": len(same_service_near_pages),
        "same_service_exact_duplicate_pages": len(same_service_exact_pages),
        "service_similarity_clusters": service_clusters[:100],
        "max_simhash_distance": max_simhash_distance,
    }


def evaluate(
    audit: dict,
    *,
    max_unreachable: int = 0,
    max_deep_pages: int | None = None,
    max_same_service_exact_pages: int = 0,
) -> list[str]:
    breaches: list[str] = []
    if int(audit.get("unreachable_from_home_pages", 0)) > max_unreachable:
        breaches.append(
            f"unreachable_from_home_pages={audit['unreachable_from_home_pages']} > {max_unreachable}"
        )
    if max_deep_pages is not None and int(audit.get("deep_indexable_pages", 0)) > max_deep_pages:
        breaches.append(f"deep_indexable_pages={audit['deep_indexable_pages']} > {max_deep_pages}")
    if int(audit.get("same_service_exact_duplicate_pages", 0)) > max_same_service_exact_pages:
        breaches.append(
            "same_service_exact_duplicate_pages="
            f"{audit['same_service_exact_duplicate_pages']} > {max_same_service_exact_pages}"
        )
    return breaches


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/var/www/x-gu.ru/current"))
    parser.add_argument("--base-url", default="https://x-gu.ru")
    parser.add_argument("--deep-threshold", type=int, default=3)
    parser.add_argument("--simhash-distance", type=int, default=6)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--max-unreachable", type=int, default=0)
    parser.add_argument("--max-deep-pages", type=int, default=None)
    parser.add_argument("--max-same-service-exact-pages", type=int, default=0)
    args = parser.parse_args()

    if not args.root.is_dir():
        print(f"Root not found: {args.root}", file=sys.stderr)
        return 2
    try:
        audit = run_audit(
            args.root,
            base_url=args.base_url,
            deep_threshold=args.deep_threshold,
            max_simhash_distance=args.simhash_distance,
        )
    except (OSError, UnicodeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Link/cluster SEO audit failed: {exc}", file=sys.stderr)
        return 2

    print("Link graph + service-cluster SEO audit")
    print(f"  indexable pages:                 {audit['indexable_pages']}")
    print(f"  reachable from home:             {audit['reachable_from_home']}")
    print(f"  unreachable from home:           {audit['unreachable_from_home_pages']}")
    print(f"  max crawl depth:                 {audit['max_crawl_depth']}")
    print(f"  pages deeper than {audit['deep_threshold']}:           {audit['deep_indexable_pages']}")
    print(f"  same-service exact dup pages:    {audit['same_service_exact_duplicate_pages']}")
    print(f"  same-service near-dup pages:     {audit['same_service_near_duplicate_pages']}")
    print(f"  crawl depth distribution:        {audit['crawl_depth_distribution']}")

    if audit["service_similarity_clusters"]:
        print("  largest same-service similarity clusters:")
        for item in audit["service_similarity_clusters"][:10]:
            print(
                f"    {item['service']}: pages={item['indexable_pages']} "
                f"near_dup={item['near_duplicate_pages']} exact_dup={item['exact_duplicate_pages']}"
            )

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"  JSON report: {args.json_out}")

    breaches = evaluate(
        audit,
        max_unreachable=args.max_unreachable,
        max_deep_pages=args.max_deep_pages,
        max_same_service_exact_pages=args.max_same_service_exact_pages,
    )
    if args.strict and breaches:
        print("Strict link/cluster SEO gate: FAIL", file=sys.stderr)
        for breach in breaches:
            print(f"  - {breach}", file=sys.stderr)
        return 3
    if args.strict:
        print("Strict link/cluster SEO gate: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
