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


def _page_metrics(pair: dict, url: str) -> dict[str, float]:
    metrics = {"impressions": 0.0, "clicks": 0.0, "position_weight": 0.0}
    for conflict in pair.get("query_details") or []:
        for page in conflict.get("pages") or []:
            if page.get("url") != url:
                continue
            impressions = float(page.get("impressions") or 0.0)
            metrics["impressions"] += impressions
            metrics["clicks"] += float(page.get("clicks") or 0.0)
            metrics["position_weight"] += float(page.get("position") or 0.0) * impressions
    return metrics


def _fallback_pair_metrics(pair: dict, url: str) -> dict[str, float]:
    """Use pair-level totals when older cannibalization JSON has no query_details."""
    pages = list(pair.get("pages") or [])
    if url not in pages:
        return {"impressions": 0.0, "clicks": 0.0, "position_weight": 0.0}
    shared = float(pair.get("shared_impressions") or 0.0)
    # We cannot reconstruct the split from older reports. Give equal evidence
    # and let quality/manual review decide instead of inventing precision.
    share = shared / max(1, len(pages))
    return {"impressions": share, "clicks": 0.0, "position_weight": 0.0}


def _score(metrics: dict[str, float], quality: dict | None) -> tuple[int, float, float, float]:
    hard_fail = _hard_fail(quality)
    impressions = metrics["impressions"]
    clicks = metrics["clicks"]
    position = metrics["position_weight"] / impressions if impressions else 999.0
    # Lexicographic score: a clean page always beats a hard-failing page;
    # then clicks, impressions, and better average position decide.
    return (0 if hard_fail else 1, clicks, impressions, -position)


def build_review(cannibalization: dict, quality_payload: dict) -> dict:
    quality = _quality_map(quality_payload)
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

        candidates = []
        for url in pages:
            metrics = _page_metrics(pair, url)
            if not metrics["impressions"] and not metrics["clicks"]:
                metrics = _fallback_pair_metrics(pair, url)
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
                    "score": _score(metrics, q),
                }
            )

        ranked = sorted(candidates, key=lambda item: item["score"], reverse=True)
        primary = ranked[0]
        alternatives = ranked[1:]
        reviews.append(
            {
                "city": pair.get("city", ""),
                "shared_queries": int(pair.get("shared_queries") or 0),
                "shared_impressions": float(pair.get("shared_impressions") or 0.0),
                "queries": list(pair.get("queries") or []),
                "recommended_primary": primary["url"],
                "recommendation_confidence": (
                    "low" if primary["quality_status"] == "missing_quality" else "review"
                ),
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
