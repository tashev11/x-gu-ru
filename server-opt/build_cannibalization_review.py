#!/usr/bin/env python3
"""Build a review-only consolidation plan from GSC cannibalization + page quality.

This tool never writes redirects, canonicals, robots directives, or policy. It
ranks same-city competing pages and proposes a primary URL for human review.
The recommendation combines search performance with hard page-quality status.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


DEFAULT_CANNIBALIZATION = Path("/opt/p3-app/data/gsc_cannibalization.json")
DEFAULT_QUALITY = Path("/opt/p3-app/data/pair_quality.json")
DEFAULT_OUT = Path("/opt/p3-app/data/cannibalization.review.json")


def _quality_map(payload: dict) -> dict[str, dict]:
    rows = payload.get("pages") or payload.get("pairs") or []
    if not isinstance(rows, list):
        raise ValueError("quality payload pages/pairs must be an array")
    result: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        if url:
            result[url] = row
    return result


def _hard_fail(row: dict | None) -> bool:
    if not row:
        return True
    if str(row.get("status") or "").strip() == "improve_before_index":
        return True
    hard = row.get("hard_failures") or row.get("hard_reasons") or []
    return bool(hard)


def _query_metric_index(cannibalization: dict) -> dict[tuple[str, str], dict[str, float]]:
    """Aggregate real query/page metrics for every competing page pair."""
    index: dict[tuple[str, str], dict[str, float]] = {}
    conflicts = cannibalization.get("query_conflicts") or []
    if not isinstance(conflicts, list):
        raise ValueError("query_conflicts must be an array")

    for conflict in conflicts:
        if not isinstance(conflict, dict):
            continue
        pages = conflict.get("pages") or []
        if not isinstance(pages, list):
            continue
        urls = [str(page.get("url") or "") for page in pages if isinstance(page, dict)]
        for left_index, left_url in enumerate(urls):
            if not left_url:
                continue
            for right_url in urls[left_index + 1 :]:
                if not right_url:
                    continue
                pair_key = tuple(sorted((left_url, right_url)))
                for page in pages:
                    if not isinstance(page, dict):
                        continue
                    url = str(page.get("url") or "")
                    if url not in pair_key:
                        continue
                    metric_key = ("|".join(pair_key), url)
                    item = index.setdefault(
                        metric_key,
                        {"impressions": 0.0, "clicks": 0.0, "position_weight": 0.0},
                    )
                    impressions = float(page.get("impressions") or 0.0)
                    item["impressions"] += impressions
                    item["clicks"] += float(page.get("clicks") or 0.0)
                    item["position_weight"] += float(page.get("position") or 0.0) * impressions
    return index


def _fallback_pair_metrics(pair: dict, url: str) -> dict[str, float]:
    pages = list(pair.get("pages") or [])
    if url not in pages:
        return {"impressions": 0.0, "clicks": 0.0, "position_weight": 0.0}
    shared = float(pair.get("shared_impressions") or 0.0)
    share = shared / max(1, len(pages))
    return {"impressions": share, "clicks": 0.0, "position_weight": 0.0}


def _score(metrics: dict[str, float], quality: dict | None) -> tuple[int, float, float, float]:
    hard_fail = _hard_fail(quality)
    impressions = metrics["impressions"]
    clicks = metrics["clicks"]
    position = metrics["position_weight"] / impressions if impressions else 999.0
    # A clean page always beats a hard-failing page. Search performance then
    # breaks ties: clicks, impressions, and finally better average position.
    return (0 if hard_fail else 1, clicks, impressions, -position)


def build_review(cannibalization: dict, quality_payload: dict) -> dict:
    quality = _quality_map(quality_payload)
    metric_index = _query_metric_index(cannibalization)
    source_pairs = cannibalization.get("same_city_pairs") or []
    if not isinstance(source_pairs, list):
        raise ValueError("same_city_pairs must be an array")

    reviews: list[dict] = []
    for pair in source_pairs:
        if not isinstance(pair, dict):
            continue
        pages = [str(url) for url in pair.get("pages") or [] if str(url).strip()]
        if len(pages) < 2:
            continue
        pair_key = tuple(sorted(pages[:2]))
        pair_id = "|".join(pair_key)

        candidates = []
        for url in pair_key:
            metrics = metric_index.get((pair_id, url))
            metric_source = "query_conflicts"
            if metrics is None:
                metrics = _fallback_pair_metrics(pair, url)
                metric_source = "pair_fallback"
            q = quality.get(url)
            impressions = metrics["impressions"]
            avg_position = metrics["position_weight"] / impressions if impressions else None
            candidates.append(
                {
                    "url": url,
                    "quality_status": (q or {}).get("status", "missing_quality"),
                    "hard_quality_fail": _hard_fail(q),
                    "impressions": impressions,
                    "clicks": metrics["clicks"],
                    "position": avg_position,
                    "metric_source": metric_source,
                    "score": _score(metrics, q),
                }
            )

        ranked = sorted(candidates, key=lambda item: item["score"], reverse=True)
        primary = ranked[0]
        alternatives = ranked[1:]
        confidence = "review"
        if primary["quality_status"] == "missing_quality" or primary["metric_source"] != "query_conflicts":
            confidence = "low"
        if primary["hard_quality_fail"]:
            confidence = "fix-first"

        reviews.append(
            {
                "city": pair.get("city", ""),
                "shared_queries": int(pair.get("shared_queries") or 0),
                "shared_impressions": float(pair.get("shared_impressions") or 0.0),
                "queries": list(pair.get("queries") or []),
                "recommended_primary": primary["url"],
                "recommendation_confidence": confidence,
                "candidates": [
                    {key: value for key, value in item.items() if key != "score"}
                    for item in ranked
                ],
                "review_actions": [
                    {
                        "url": item["url"],
                        "suggestion": "compare intent; merge/redirect only if intent is truly the same",
                    }
                    for item in alternatives
                ],
            }
        )

    reviews.sort(key=lambda item: (-item["shared_queries"], -item["shared_impressions"], item["city"]))
    return {
        "source_same_city_pairs": len(source_pairs),
        "review_pairs": len(reviews),
        "automatic_changes": False,
        "warning": (
            "Do not redirect merely because two pages share queries. Confirm same search intent, "
            "content overlap, conversions, links and business purpose first."
        ),
        "reviews": reviews,
    }


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
    parser.add_argument("--cannibalization", type=Path, default=DEFAULT_CANNIBALIZATION)
    parser.add_argument("--quality", type=Path, default=DEFAULT_QUALITY)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--apply", action="store_true", help="write review JSON only; never changes production SEO")
    args = parser.parse_args()

    if not args.cannibalization.is_file() or not args.quality.is_file():
        print("Required cannibalization/quality input is missing", file=sys.stderr)
        return 2
    try:
        cannibalization = json.loads(args.cannibalization.read_text(encoding="utf-8", errors="strict"))
        quality = json.loads(args.quality.read_text(encoding="utf-8", errors="strict"))
        review = build_review(cannibalization, quality)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"Cannot build cannibalization review: {exc}", file=sys.stderr)
        return 2

    print("Cannibalization consolidation review")
    print(f"  same-city pairs: {review['source_same_city_pairs']}")
    print(f"  review plans:    {review['review_pairs']}")
    print("  SAFETY: no redirects/canonicals/policy changes are applied automatically.")
    if not args.apply:
        print(f"[DRY-RUN] would write: {args.out}")
        return 0
    try:
        _atomic_write(args.out, review)
    except Exception as exc:  # noqa: BLE001
        print(f"Cannot write review: {exc}", file=sys.stderr)
        return 3
    print(f"[WRITTEN FOR REVIEW] {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
