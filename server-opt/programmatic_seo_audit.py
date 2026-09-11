#!/usr/bin/env python3
"""Full-corpus programmatic SEO audit for x-gu.ru releases.

Unlike the historical sample-based uniqueness script, this tool examines every
HTML page in a release and evaluates only the pages that the release policy says
should be indexable. It is intentionally read-only.

Signals:
- physical vs indexable/noindex inventory;
- thin indexable pages;
- indexable orphan pages (no inbound link from another indexable page);
- links from indexable pages to policy-closed pages;
- exact duplicate visible bodies;
- near-duplicate visible bodies using 64-bit SimHash + banded candidate lookup.

The near-duplicate check is a risk signal, not a search-engine verdict. Run it
against a real release and review the largest clusters before expanding index
coverage.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from seo_healthcheck import (  # noqa: E402
    PageParser,
    _default_keep_config,
    _default_whitelist,
    _expected_indexable,
    _internal_page_target,
    _load_index_policy,
    _normalize_base_url,
    _normalize_url,
    _page_url,
)


TOKEN_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
NUMBER_RE = re.compile(r"\b\d+(?:[.,]\d+)?\b")
SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class PageRecord:
    url: str
    path: Path
    indexable: bool
    words: int
    body: str
    hrefs: tuple[str, ...]


def _normalized_body(text: str) -> str:
    text = NUMBER_RE.sub("#", text.lower())
    return SPACE_RE.sub(" ", text).strip()


def _tokens(text: str) -> list[str]:
    return [token for token in TOKEN_RE.findall(_normalized_body(text)) if len(token) > 1]


def _simhash64(text: str, shingle_size: int = 4) -> int:
    tokens = _tokens(text)
    if not tokens:
        return 0
    if len(tokens) < shingle_size:
        features = tokens
    else:
        features = [" ".join(tokens[i : i + shingle_size]) for i in range(len(tokens) - shingle_size + 1)]

    counts = Counter(features)
    vector = [0] * 64
    for feature, weight in counts.items():
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        value = int.from_bytes(digest, "big")
        for bit in range(64):
            vector[bit] += weight if value & (1 << bit) else -weight

    result = 0
    for bit, score in enumerate(vector):
        if score >= 0:
            result |= 1 << bit
    return result


def _near_duplicate_groups(hashes: dict[str, int], max_distance: int) -> list[list[str]]:
    # Eight 8-bit bands guarantee that any pair with Hamming distance <= 7
    # shares at least one unchanged band (pigeonhole principle). We therefore
    # cannot silently miss a pair that is inside the supported threshold.
    if max_distance < 0 or max_distance > 7:
        raise ValueError("max_distance must be between 0 and 7")

    parent = {url: url for url in hashes}

    def find(item: str) -> str:
        while parent[item] != item:
            parent[item] = parent[parent[item]]
            item = parent[item]
        return item

    def union(left: str, right: str) -> None:
        a, b = find(left), find(right)
        if a != b:
            parent[b] = a

    buckets: dict[tuple[int, int], list[str]] = defaultdict(list)
    compared: set[tuple[str, str]] = set()
    for url, value in hashes.items():
        candidates: set[str] = set()
        for band in range(8):
            key = (band, (value >> (band * 8)) & 0xFF)
            candidates.update(buckets[key])
        for other in candidates:
            pair = (other, url) if other < url else (url, other)
            if pair in compared:
                continue
            compared.add(pair)
            if (value ^ hashes[other]).bit_count() <= max_distance:
                union(url, other)
        for band in range(8):
            key = (band, (value >> (band * 8)) & 0xFF)
            buckets[key].append(url)

    groups: dict[str, list[str]] = defaultdict(list)
    for url in hashes:
        groups[find(url)].append(url)
    result = [sorted(group) for group in groups.values() if len(group) > 1]
    result.sort(key=lambda group: (-len(group), group[0]))
    return result


def _page_type(url: str) -> str:
    parts = [part for part in urlparse(url).path.split("/") if part]
    if not parts:
        return "home"
    if parts == ["privacy"]:
        return "utility"
    if len(parts) == 1:
        return "city_hub"
    if len(parts) == 2:
        return "service_landing"
    return "other"


def collect_pages(root: Path, base_url: str) -> tuple[list[PageRecord], dict]:
    root = root.resolve()
    base_url = _normalize_base_url(base_url)
    keep_config = _default_keep_config(root).resolve()
    whitelist = _default_whitelist(root, keep_config).resolve()
    policy = _load_index_policy(keep_config, whitelist, base_url)
    if policy is None:
        raise RuntimeError("release policy/whitelist could not be loaded; refusing corpus SEO audit")

    records: list[PageRecord] = []
    for html_file in sorted(root.rglob("index.html")):
        html = html_file.read_text(encoding="utf-8", errors="ignore")
        parser = PageParser()
        try:
            parser.feed(html)
        except Exception:
            pass
        url = _normalize_url(_page_url(root, html_file, base_url))
        expected = _expected_indexable(url, base_url, policy)
        if expected is None:
            raise RuntimeError(f"indexability could not be resolved for {url}")
        words = len(TOKEN_RE.findall(parser.body_text))
        records.append(
            PageRecord(
                url=url,
                path=html_file,
                indexable=bool(expected),
                words=words,
                body=parser.body_text,
                hrefs=tuple(parser.hrefs),
            )
        )
    return records, policy


def run_audit(
    root: Path,
    *,
    base_url: str = "https://x-gu.ru",
    min_words: int = 300,
    max_simhash_distance: int = 6,
) -> dict:
    if min_words < 1:
        raise ValueError("min_words must be positive")

    records, _policy = collect_pages(root, base_url)
    by_url = {record.url: record for record in records}
    indexable = [record for record in records if record.indexable]
    indexable_urls = {record.url for record in indexable}
    closed_urls = set(by_url) - indexable_urls

    inbound: Counter[str] = Counter()
    links_to_closed: dict[str, set[str]] = defaultdict(set)
    for record in indexable:
        seen_targets: set[str] = set()
        for href in record.hrefs:
            target = _internal_page_target(href, record.url, base_url)
            if target is None or target in seen_targets:
                continue
            seen_targets.add(target)
            if target in indexable_urls and target != record.url:
                inbound[target] += 1
            elif target in closed_urls:
                links_to_closed[record.url].add(target)

    orphan_urls = sorted(
        record.url
        for record in indexable
        if _page_type(record.url) not in {"home", "utility"} and inbound[record.url] == 0
    )
    thin_urls = sorted(record.url for record in indexable if record.words < min_words)

    exact_buckets: dict[str, list[str]] = defaultdict(list)
    simhashes: dict[str, int] = {}
    for record in indexable:
        normalized = _normalized_body(record.body)
        exact = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        exact_buckets[exact].append(record.url)
        simhashes[record.url] = _simhash64(normalized)

    exact_groups = [sorted(group) for group in exact_buckets.values() if len(group) > 1]
    exact_groups.sort(key=lambda group: (-len(group), group[0]))
    near_groups = _near_duplicate_groups(simhashes, max_simhash_distance)

    page_types = Counter(_page_type(record.url) for record in indexable)
    link_to_closed_pairs = sum(len(targets) for targets in links_to_closed.values())
    near_pages = len({url for group in near_groups for url in group})
    exact_pages = len({url for group in exact_groups for url in group})

    return {
        "root": str(root.resolve()),
        "base_url": _normalize_base_url(base_url),
        "physical_pages": len(records),
        "indexable_pages": len(indexable),
        "closed_pages": len(records) - len(indexable),
        "indexable_page_types": dict(sorted(page_types.items())),
        "min_words": min_words,
        "thin_indexable_pages": len(thin_urls),
        "thin_indexable_urls": thin_urls,
        "orphan_indexable_pages": len(orphan_urls),
        "orphan_indexable_urls": orphan_urls,
        "indexable_pages_linking_to_closed": len(links_to_closed),
        "indexable_links_to_closed": link_to_closed_pairs,
        "links_to_closed_examples": {
            source: sorted(targets)[:20] for source, targets in sorted(links_to_closed.items())[:50]
        },
        "exact_duplicate_pages": exact_pages,
        "exact_duplicate_groups": exact_groups[:50],
        "near_duplicate_pages": near_pages,
        "near_duplicate_groups": near_groups[:50],
        "max_simhash_distance": max_simhash_distance,
    }


def evaluate(
    audit: dict,
    *,
    max_thin: int = 0,
    max_orphans: int = 0,
    max_links_to_closed: int = 0,
    max_near_duplicate_pages: int | None = None,
) -> list[str]:
    breaches: list[str] = []
    checks = (
        ("thin_indexable_pages", max_thin),
        ("orphan_indexable_pages", max_orphans),
        ("indexable_links_to_closed", max_links_to_closed),
    )
    for key, limit in checks:
        if int(audit.get(key, 0)) > limit:
            breaches.append(f"{key}={audit[key]} > {limit}")
    if max_near_duplicate_pages is not None and int(audit.get("near_duplicate_pages", 0)) > max_near_duplicate_pages:
        breaches.append(
            f"near_duplicate_pages={audit['near_duplicate_pages']} > {max_near_duplicate_pages}"
        )
    return breaches


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/var/www/x-gu.ru/current"))
    parser.add_argument("--base-url", default="https://x-gu.ru")
    parser.add_argument("--min-words", type=int, default=300)
    parser.add_argument("--simhash-distance", type=int, default=6)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--strict", action="store_true", help="return non-zero when strict limits are breached")
    parser.add_argument("--max-thin", type=int, default=0)
    parser.add_argument("--max-orphans", type=int, default=0)
    parser.add_argument("--max-links-to-closed", type=int, default=0)
    parser.add_argument(
        "--max-near-duplicate-pages",
        type=int,
        default=None,
        help="optional strict threshold; omitted means near-duplicates are reported but do not fail",
    )
    args = parser.parse_args()

    if not args.root.is_dir():
        print(f"Root not found: {args.root}", file=sys.stderr)
        return 2

    try:
        audit = run_audit(
            args.root,
            base_url=args.base_url,
            min_words=args.min_words,
            max_simhash_distance=args.simhash_distance,
        )
    except (OSError, UnicodeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Programmatic SEO audit failed: {exc}", file=sys.stderr)
        return 2

    print("Programmatic SEO corpus audit")
    print(f"  physical pages:               {audit['physical_pages']}")
    print(f"  indexable pages:              {audit['indexable_pages']}")
    print(f"  policy-closed pages:          {audit['closed_pages']}")
    print(f"  indexable types:              {audit['indexable_page_types']}")
    print(f"  thin indexable (<{audit['min_words']} words): {audit['thin_indexable_pages']}")
    print(f"  orphan indexable pages:       {audit['orphan_indexable_pages']}")
    print(f"  open->closed internal links:  {audit['indexable_links_to_closed']}")
    print(f"  exact duplicate pages:        {audit['exact_duplicate_pages']}")
    print(
        f"  near-duplicate pages:         {audit['near_duplicate_pages']} "
        f"(SimHash distance <= {audit['max_simhash_distance']})"
    )

    if audit["near_duplicate_groups"]:
        print("  largest near-duplicate groups:")
        for group in audit["near_duplicate_groups"][:10]:
            print(f"    {len(group)} pages: {', '.join(group[:4])}{' ...' if len(group) > 4 else ''}")

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"  JSON report: {args.json_out}")

    breaches = evaluate(
        audit,
        max_thin=args.max_thin,
        max_orphans=args.max_orphans,
        max_links_to_closed=args.max_links_to_closed,
        max_near_duplicate_pages=args.max_near_duplicate_pages,
    )
    if args.strict and breaches:
        print("Strict programmatic SEO gate: FAIL", file=sys.stderr)
        for breach in breaches:
            print(f"  - {breach}", file=sys.stderr)
        return 3
    if args.strict:
        print("Strict programmatic SEO gate: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
