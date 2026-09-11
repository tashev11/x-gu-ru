#!/usr/bin/env python3
"""Build a review-only pair-level SEO policy candidate.

Search evidence answers "does this URL show demand/value?". Pair quality answers
"is the current page safe enough to recommend for index?". The output always
remains ``example_only=true`` and therefore cannot be promoted automatically.
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
DEFAULT_QUALITY = Path("/opt/p3-app/data/pair_quality.json")
DEFAULT_OUT = Path("/opt/p3-app/data/index_policy.v2.candidate.json")
DEFAULT_REVIEW = Path("/opt/p3-app/data/index_policy.v2.review.json")
BASE_HOST = "x-gu.ru"


def _pair_from_url(url: str) -> tuple[str, str] | None:
    parsed = urlparse(str(url).strip())
    if parsed.scheme != "https" or parsed.hostname != BASE_HOST or parsed.query or parsed.fragment:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    return (parts[0], parts[1]) if len(parts) == 2 else None


def _city_from_url(url: str) -> str | None:
    parsed = urlparse(str(url).strip())
    if parsed.scheme != "https" or parsed.hostname != BASE_HOST or parsed.query or parsed.fragment:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    return parts[0] if len(parts) == 1 else None


def _parse_report_date(value: object, label: str) -> date:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError(f"{label} is missing")
    try:
        return date.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError(f"{label} is not ISO YYYY-MM-DD: {raw!r}") from exc


def validate_input_freshness(
    evidence_payload: dict,
    quality_payload: dict | None,
    *,
    max_age_days: int = 14,
    today: date | None = None,
) -> dict:
    if max_age_days < 0:
        raise ValueError("max_age_days must be non-negative")
    today = today or date.today()
    evidence_date = _parse_report_date(evidence_payload.get("generated_at"), "search evidence generated_at")
    evidence_age = (today - evidence_date).days
    if evidence_age < 0:
        raise ValueError("search evidence generated_at is in the future")
    if evidence_age > max_age_days:
        raise ValueError(f"search evidence is stale: age={evidence_age}d > {max_age_days}d")

    result = {"evidence_age_days": evidence_age, "quality_age_days": None}
    if quality_payload is None:
        return result

    quality_date = _parse_report_date(quality_payload.get("generated_at"), "pair quality generated_at")
    quality_age = (today - quality_date).days
    if quality_age < 0:
        raise ValueError("pair quality generated_at is in the future")
    if quality_age > max_age_days:
        raise ValueError(f"pair quality report is stale: age={quality_age}d > {max_age_days}d")

    quality_source = str(quality_payload.get("source_evidence_generated_at") or "").strip()
    evidence_source = str(evidence_payload.get("generated_at") or "").strip()
    if quality_source != evidence_source:
        raise ValueError(
            "pair quality report was built from a different search evidence snapshot: "
            f"quality={quality_source!r} evidence={evidence_source!r}"
        )
    result["quality_age_days"] = quality_age
    return result


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


def _quality_map(payload: dict | None) -> dict[str, dict]:
    if payload is None:
        return {}
    rows = payload.get("pairs") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("pair quality report must contain a 'pairs' array")
    result: dict[str, dict] = {}
    for row in rows:
        if isinstance(row, dict) and str(row.get("url") or "").strip():
            result[str(row["url"]).strip()] = row
    return result


def build_candidate(
    evidence_payload: dict,
    *,
    quality_payload: dict | None = None,
    require_quality: bool = False,
    min_impressions: float = 5.0,
    min_clicks: float = 1.0,
) -> tuple[dict, dict]:
    rows = evidence_payload.get("urls") or []
    if not isinstance(rows, list):
        raise ValueError("search evidence 'urls' must be an array")
    quality = _quality_map(quality_payload)

    selected_pairs: dict[tuple[str, str], dict] = {}
    rejected_pairs: list[dict] = []
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
        quality_row = quality.get(url)
        quality_state = str((quality_row or {}).get("quality_state") or "unknown")
        quality_flags = list((quality_row or {}).get("flags") or [])

        reject_reason: str | None = None
        if quality_state == "improve_before_index":
            reject_reason = "quality_hard_fail"
        elif require_quality and quality_row is None:
            reject_reason = "quality_missing"
        elif require_quality and quality_state not in {"clean", "review_similarity"}:
            reject_reason = "quality_unknown"

        review_row = {
            "url": url,
            "city": city_slug,
            "service": service_slug,
            "reasons": reasons,
            "yandex_in_search": bool(raw.get("yandex_in_search")),
            "gsc_impressions": float(raw.get("gsc_impressions") or 0.0),
            "gsc_clicks": float(raw.get("gsc_clicks") or 0.0),
            "gsc_position": raw.get("gsc_position"),
            "manual_protected": bool(raw.get("manual_protected")),
            "quality_state": quality_state,
            "quality_flags": quality_flags,
        }

        if reject_reason is not None:
            rejected[reject_reason] += 1
            review_row["recommendation"] = "improve_before_index"
            review_row["rejection_reason"] = reject_reason
            rejected_pairs.append(review_row)
            continue

        selected_city_hubs.add(city_slug)
        review_row["recommendation"] = (
            "review_similarity" if quality_state == "review_similarity" else "candidate_open"
        )
        selected_pairs[pair] = review_row

    pairs = sorted(selected_pairs)
    open_cities = sorted(selected_city_hubs | {city for city, _service in pairs})
    policy = {
        "policy_version": 2,
        "example_only": True,
        "reviewed_at": None,
        "source_note": (
            "AUTO-GENERATED REVIEW CANDIDATE from combined Yandex/GSC/manual evidence"
            + (" intersected with pair-quality audit." if quality_payload is not None else ".")
            + " Review exact pairs before setting example_only=false."
        ),
        "open_cities": open_cities,
        "open_pairs": [f"{city}/{service}" for city, service in pairs],
    }
    review = {
        "generated_at": date.today().isoformat(),
        "source_evidence_generated_at": evidence_payload.get("generated_at"),
        "source_quality_generated_at": (quality_payload or {}).get("generated_at"),
        "quality_report_used": quality_payload is not None,
        "quality_required": require_quality,
        "thresholds": {
            "gsc_min_impressions": min_impressions,
            "gsc_min_clicks": min_clicks,
        },
        "counts": {
            "evidence_rows": len(rows),
            "candidate_city_hubs": len(open_cities),
            "candidate_pairs": len(pairs),
            "rejected_below_threshold": rejected["below_threshold"],
            "rejected_quality_hard_fail": rejected["quality_hard_fail"],
            "rejected_quality_missing": rejected["quality_missing"],
            "rejected_quality_unknown": rejected["quality_unknown"],
            "rejected_non_pair_url": rejected["non_pair_url"],
            "invalid_records": rejected["invalid_record"],
        },
        "pairs": [selected_pairs[pair] for pair in pairs],
        "rejected_pairs": sorted(rejected_pairs, key=lambda row: row["url"]),
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
    parser.add_argument("--quality", type=Path, default=DEFAULT_QUALITY)
    parser.add_argument("--skip-quality", action="store_true", help="build evidence-only candidate; review use only")
    parser.add_argument("--max-input-age-days", type=int, default=14)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--review-out", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--gsc-min-impressions", type=float, default=5.0)
    parser.add_argument("--gsc-min-clicks", type=float, default=1.0)
    parser.add_argument("--apply", action="store_true", help="write review-only candidate files")
    args = parser.parse_args()

    if args.gsc_min_impressions < 0 or args.gsc_min_clicks < 0 or args.max_input_age_days < 0:
        print("thresholds/age must be non-negative", file=sys.stderr)
        return 2
    if not args.evidence.is_file():
        print(f"Evidence file not found: {args.evidence}", file=sys.stderr)
        return 2
    if not args.skip_quality and not args.quality.is_file():
        print(
            f"Pair quality report not found: {args.quality}. Run pair_quality_audit.py first or use --skip-quality for evidence-only review.",
            file=sys.stderr,
        )
        return 2

    try:
        evidence = json.loads(args.evidence.read_text(encoding="utf-8", errors="strict"))
        quality = None if args.skip_quality else json.loads(args.quality.read_text(encoding="utf-8", errors="strict"))
        freshness = validate_input_freshness(
            evidence,
            quality,
            max_age_days=args.max_input_age_days,
        )
        policy, review = build_candidate(
            evidence,
            quality_payload=quality,
            require_quality=not args.skip_quality,
            min_impressions=args.gsc_min_impressions,
            min_clicks=args.gsc_min_clicks,
        )
        review["freshness"] = freshness
        review["max_input_age_days"] = args.max_input_age_days
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"Cannot build pair-policy candidate: {exc}", file=sys.stderr)
        return 2

    print("Pair-level SEO policy candidate")
    print(f"  evidence rows:              {review['counts']['evidence_rows']}")
    print(f"  evidence age:               {freshness['evidence_age_days']}d")
    print(f"  quality report age:         {freshness['quality_age_days']}d")
    print(f"  quality report used:        {review['quality_report_used']}")
    print(f"  city hubs:                  {review['counts']['candidate_city_hubs']}")
    print(f"  exact candidate pairs:      {review['counts']['candidate_pairs']}")
    print(f"  below search threshold:     {review['counts']['rejected_below_threshold']}")
    print(f"  quality hard-fail rejected: {review['counts']['rejected_quality_hard_fail']}")
    print(f"  quality missing/unknown:    {review['counts']['rejected_quality_missing'] + review['counts']['rejected_quality_unknown']}")
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
