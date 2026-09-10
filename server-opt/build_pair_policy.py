#!/usr/bin/env python3
"""Build a review-only pair-level SEO policy candidate from search evidence.

The output is deliberately ``example_only=true``. It cannot be applied by
``shrink_index.py`` until a human reviews the exact city/service pairs, records
review metadata, and explicitly promotes the policy.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_EVIDENCE = Path("/opt/p3-app/data/search_evidence.json")
DEFAULT_OUT = Path("/opt/p3-app/data/index_policy.v2.candidate.json")
DEFAULT_REVIEW = Path("/opt/p3-app/data/index_policy.v2.review.json")
BASE_HOST = "x-gu.ru"


def _pair_from_url(url: str) -> tuple[str, str] | None:
    parsed = urlparse(str(url).strip())
    if parsed.scheme != "https" or parsed.hostname != BASE_HOST or parsed.query or parsed.fragment:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2:
        return None
    return parts[0], parts[1]


def _city_from_url(url: str) -> str | None:
    parsed = urlparse(str(url).strip())
    if parsed.scheme != "https" or parsed.hostname != BASE_HOST or parsed.query or parsed.fragment:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    return parts[0] if len(parts) == 1 else None


def _signal(record: dict, *, min_impressions: float, min_clicks: float) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if bool(record.get("manual_protected")):
        reasons.append("manual")
    if bool(record.get("yandex_in_search")):
        reasons.append("yandex")
    clicks = float(record.get("gsc_clicks") or 0.0)
    impressions = float(record.get("gsc_impressions") or 0.0)
    if clicks >= min_clicks:
        reasons.append(f"gsc_clicks>={min_clicks:g}")
    if impressions >= min_impressions:
        reasons.append(f"gsc_impressions>={min_impressions:g}")
    return bool(reasons), reasons


def build_candidate(
    evidence_payload: dict,
    *,
    min_impressions: float = 5.0,
    min_clicks: float = 1.0,
) -> tuple[dict, dict]:
    rows = evidence_payload.get("urls") or []
    if not isinstance(rows, list):
        raise ValueError("search evidence 'urls' must be an array")

    selected_pairs: dict[tuple[str, str], dict] = {}
    selected_city_hubs: set[str] = set()
    rejected = Counter()

    for raw in rows:
        if not isinstance(raw, dict):
            rejected["invalid_record"] += 1
            continue
        url = str(raw.get("url") or "").strip()
        pair = _pair_from_url(url)
        city = _city_from_url(url)
        qualified, reasons = _signal(raw, min_impressions=min_impressions, min_clicks=min_clicks)

        if city is not None and qualified:
            selected_city_hubs.add(city)
            continue
        if pair is None:
            if city is None:
                rejected["non_pair_url"] += 1
            continue
        if not qualified:
            rejected["below_threshold"] += 1
            continue

        city_slug, service_slug = pair
        selected_city_hubs.add(city_slug)
        selected_pairs[pair] = {
            "url": url,
            "city": city_slug,
            "service": service_slug,
            "reasons": reasons,
            "yandex_in_search": bool(raw.get("yandex_in_search")),
            "gsc_impressions": float(raw.get("gsc_impressions") or 0.0),
            "gsc_clicks": float(raw.get("gsc_clicks") or 0.0),
            "gsc_position": raw.get("gsc_position"),
            "manual_protected": bool(raw.get("manual_protected")),
        }

    pairs = sorted(selected_pairs)
    open_cities = sorted(selected_city_hubs | {city for city, _service in pairs})
    policy = {
        "policy_version": 2,
        "example_only": True,
        "reviewed_at": None,
        "source_note": (
            "AUTO-GENERATED REVIEW CANDIDATE from combined Yandex/GSC/manual search evidence. "
            "Review exact pairs and page quality before setting example_only=false."
        ),
        "open_cities": open_cities,
        "open_pairs": [f"{city}/{service}" for city, service in pairs],
    }
    review = {
        "generated_at": date.today().isoformat(),
        "source_evidence_generated_at": evidence_payload.get("generated_at"),
        "thresholds": {
            "gsc_min_impressions": min_impressions,
            "gsc_min_clicks": min_clicks,
        },
        "counts": {
            "evidence_rows": len(rows),
            "candidate_city_hubs": len(open_cities),
            "candidate_pairs": len(pairs),
            "rejected_below_threshold": rejected["below_threshold"],
            "rejected_non_pair_url": rejected["non_pair_url"],
            "invalid_records": rejected["invalid_record"],
        },
        "pairs": [selected_pairs[pair] for pair in pairs],
    }
    return policy, review


def _atomic_write(path: Path, text: str) -> None:
    if not path.parent.is_dir():
        raise FileNotFoundError(f"output parent does not exist: {path.parent}")
    if path.is_symlink() or path.parent.is_symlink():
        raise RuntimeError(f"refusing symlink output path: {path}")
    temp = path.with_name(f".{path.name}.next.{os.getpid()}")
    try:
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--review-out", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--gsc-min-impressions", type=float, default=5.0)
    parser.add_argument("--gsc-min-clicks", type=float, default=1.0)
    parser.add_argument("--apply", action="store_true", help="write review-only candidate files")
    args = parser.parse_args()

    if args.gsc_min_impressions < 0 or args.gsc_min_clicks < 0:
        print("thresholds must be non-negative", file=sys.stderr)
        return 2
    if not args.evidence.is_file():
        print(f"Evidence file not found: {args.evidence}", file=sys.stderr)
        return 2
    try:
        evidence = json.loads(args.evidence.read_text(encoding="utf-8", errors="strict"))
        policy, review = build_candidate(
            evidence,
            min_impressions=args.gsc_min_impressions,
            min_clicks=args.gsc_min_clicks,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"Cannot build pair-policy candidate: {exc}", file=sys.stderr)
        return 2

    print("Pair-level SEO policy candidate")
    print(f"  evidence rows:   {review['counts']['evidence_rows']}")
    print(f"  city hubs:       {review['counts']['candidate_city_hubs']}")
    print(f"  exact pairs:     {review['counts']['candidate_pairs']}")
    print(f"  below threshold: {review['counts']['rejected_below_threshold']}")
    print("  SAFETY: output remains example_only=true and cannot be applied directly.")

    if not args.apply:
        print(f"[DRY-RUN] would write policy candidate: {args.out}")
        print(f"[DRY-RUN] would write review evidence:  {args.review_out}")
        return 0

    try:
        _atomic_write(args.out, json.dumps(policy, ensure_ascii=False, indent=2) + "\n")
        _atomic_write(args.review_out, json.dumps(review, ensure_ascii=False, indent=2) + "\n")
    except Exception as exc:  # noqa: BLE001
        print(f"Writing candidate failed: {exc}", file=sys.stderr)
        return 3

    print(f"[WRITTEN FOR REVIEW] {args.out}")
    print(f"[WRITTEN FOR REVIEW] {args.review_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
