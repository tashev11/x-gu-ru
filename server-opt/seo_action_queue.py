#!/usr/bin/env python3
"""Combine SEO reports into one review-only action queue per URL.

No action from this report is applied automatically. It exists to turn multiple
programmatic-SEO diagnostics into a prioritized human work queue.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


DEFAULT_COVERAGE = Path("/opt/p3-app/data/index_coverage.json")
DEFAULT_CANNIBALIZATION = Path("/opt/p3-app/data/cannibalization.review.json")
DEFAULT_GRAPH = Path("/opt/p3-app/data/link_graph_cluster.json")
DEFAULT_METADATA = Path("/opt/p3-app/data/metadata_intent.json")
DEFAULT_OPPORTUNITIES = Path("/opt/p3-app/data/gsc_opportunities.json")
DEFAULT_OUT = Path("/opt/p3-app/data/seo_action_queue.json")


PRIORITY = {
    "CANNIBALIZATION_REVIEW": 100,
    "IMPROVE_OPEN_PAGE": 90,
    "IMPROVE_BEFORE_OPEN": 85,
    "OPEN_REVIEW": 75,
    "INTENT_REVIEW": 70,
    "INTERNAL_LINKING": 65,
    "SNIPPET_REVIEW": 62,
    "STRIKING_DISTANCE": 58,
    "CLOSE_REVIEW": 55,
    "QUALITY_AUDIT": 45,
    "CONTENT_GROWTH": 35,
    "KEEP": 10,
}

GROWTH_CATEGORIES = {"SNIPPET_REVIEW", "STRIKING_DISTANCE", "CONTENT_GROWTH"}


def _read_json(path: Path, *, required: bool) -> dict:
    if not path.is_file():
        if required:
            raise FileNotFoundError(path)
        return {}
    payload = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be object: {path}")
    return payload


def _coverage_rows(payload: dict) -> dict[str, dict]:
    result: dict[str, dict] = {}
    cohorts = payload.get("cohorts") or {}
    if not isinstance(cohorts, dict):
        raise ValueError("coverage cohorts must be an object")
    for cohort, rows in cohorts.items():
        if not isinstance(rows, list):
            continue
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            url = str(raw.get("url") or "").strip()
            if url:
                result[url] = {**raw, "coverage_cohort": str(cohort)}
    return result


def _cannibalization_index(payload: dict) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = defaultdict(list)
    for review in payload.get("reviews") or []:
        if not isinstance(review, dict):
            continue
        for candidate in review.get("candidates") or []:
            if not isinstance(candidate, dict):
                continue
            url = str(candidate.get("url") or "").strip()
            if url:
                result[url].append(review)
    return result


def _graph_index(payload: dict) -> tuple[set[str], set[str]]:
    unreachable = {str(url) for url in payload.get("unreachable_from_home_urls") or []}
    deep = {str(url) for url in payload.get("deep_indexable_urls") or []}
    return unreachable, deep


def _metadata_index(payload: dict) -> tuple[dict[str, list[dict]], set[str]]:
    cross: dict[str, list[dict]] = defaultdict(list)
    for row in payload.get("cross_service_examples") or []:
        if not isinstance(row, dict):
            continue
        for key in ("left", "right"):
            url = str(row.get(key) or "").strip()
            if url:
                cross[url].append(row)
    risky_services = {
        str(row.get("service"))
        for row in payload.get("service_stats") or []
        if isinstance(row, dict) and row.get("template_risk") and str(row.get("service") or "").strip()
    }
    return cross, risky_services


def _opportunity_index(payload: dict) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for row in payload.get("opportunities") or []:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        if url:
            result[url] = row
    return result


def _base_action(cohort: str) -> tuple[str, list[str]]:
    mapping = {
        "open_with_signal": ("KEEP", ["policy open and current search/manual evidence exists"]),
        "open_with_signal_quality_fail": (
            "IMPROVE_OPEN_PAGE",
            ["currently indexable and has demand, but page quality has a hard failure"],
        ),
        "open_without_signal": (
            "CLOSE_REVIEW",
            ["currently indexable but current evidence window has no qualifying signal"],
        ),
        "closed_with_signal_quality_ready": (
            "OPEN_REVIEW",
            ["currently closed, demand exists and page quality is ready/reviewable"],
        ),
        "closed_with_signal_quality_fail": (
            "IMPROVE_BEFORE_OPEN",
            ["currently closed and demand exists, but page quality has a hard failure"],
        ),
        "closed_with_signal_quality_unknown": (
            "QUALITY_AUDIT",
            ["currently closed and demand exists, but quality evidence is missing/unknown"],
        ),
        "closed_without_signal": ("KEEP", ["closed and no qualifying current signal"]),
    }
    return mapping.get(cohort, ("QUALITY_AUDIT", [f"unrecognized coverage cohort: {cohort}"]))


def build_queue(
    coverage: dict,
    *,
    cannibalization: dict | None = None,
    graph: dict | None = None,
    metadata: dict | None = None,
    opportunities: dict | None = None,
) -> dict:
    coverage_rows = _coverage_rows(coverage)
    cann_index = _cannibalization_index(cannibalization or {})
    unreachable, deep = _graph_index(graph or {})
    cross_meta, risky_services = _metadata_index(metadata or {})
    opportunity_index = _opportunity_index(opportunities or {})

    items: list[dict] = []
    counts: Counter[str] = Counter()
    for url, row in coverage_rows.items():
        action, reasons = _base_action(row["coverage_cohort"])
        secondary: list[str] = []
        annotations: dict[str, object] = {}

        conflicts = cann_index.get(url, [])
        if conflicts:
            action = "CANNIBALIZATION_REVIEW"
            reasons.append(f"URL participates in {len(conflicts)} same-city competing pair review(s)")
            annotations["cannibalization"] = [
                {
                    "recommended_primary": conflict.get("recommended_primary"),
                    "shared_queries": conflict.get("shared_queries"),
                    "queries": conflict.get("queries", [])[:10],
                    "confidence": conflict.get("recommendation_confidence"),
                }
                for conflict in conflicts
            ]

        if url in unreachable:
            secondary.append("INTERNAL_LINKING")
            reasons.append("indexable URL is unreachable from homepage graph")
        elif url in deep:
            secondary.append("INTERNAL_LINKING")
            reasons.append("indexable URL is deeper than configured crawl-depth threshold")

        if cross_meta.get(url):
            secondary.append("INTENT_REVIEW")
            reasons.append("metadata is highly similar to another service page in the same city")
            annotations["metadata_intent_conflicts"] = cross_meta[url][:10]

        service = str(row.get("service") or "")
        if service in risky_services:
            reasons.append("service belongs to a high city-template-similarity metadata cluster")
            annotations["service_template_risk"] = True

        opportunity = opportunity_index.get(url)
        if opportunity is not None:
            category = str(opportunity.get("category") or "")
            annotations["gsc_opportunity"] = opportunity
            if category in GROWTH_CATEGORIES:
                secondary.append(category)
                reasons.append(
                    f"GSC growth opportunity: {category}, position={opportunity.get('position')}, "
                    f"impressions={opportunity.get('impressions')}, ctr={opportunity.get('ctr')}"
                )
                if PRIORITY.get(category, 0) > PRIORITY[action]:
                    action = category
            elif category == "CLOSED_SIGNAL_REVIEW":
                reasons.append("GSC independently reports search signal for a policy-closed URL")
                annotations["coverage_opportunity_consistency_review"] = True

        if action == "KEEP" and "INTENT_REVIEW" in secondary:
            action = "INTENT_REVIEW"
        elif action == "KEEP" and "INTERNAL_LINKING" in secondary:
            action = "INTERNAL_LINKING"

        item = {
            "url": url,
            "city": row.get("city"),
            "service": row.get("service"),
            "primary_action": action,
            "priority": PRIORITY[action],
            "secondary_actions": sorted(set(secondary)),
            "coverage_cohort": row["coverage_cohort"],
            "policy_open": bool(row.get("policy_open")),
            "has_current_signal": bool(row.get("has_current_signal")),
            "gsc_impressions": float(row.get("gsc_impressions") or 0.0),
            "gsc_clicks": float(row.get("gsc_clicks") or 0.0),
            "quality_state": row.get("quality_state"),
            "quality_flags": list(row.get("quality_flags") or []),
            "reasons": reasons,
            "annotations": annotations,
        }
        items.append(item)
        counts[action] += 1

    items.sort(
        key=lambda item: (
            -item["priority"],
            -item["gsc_clicks"],
            -item["gsc_impressions"],
            item["url"],
        )
    )
    return {
        "automatic_changes": False,
        "total_urls": len(items),
        "action_counts": dict(sorted(counts.items())),
        "source_policy_version": coverage.get("policy_version"),
        "source_policy_mode": coverage.get("policy_mode"),
        "evidence_generated_at": coverage.get("evidence_generated_at"),
        "quality_generated_at": coverage.get("quality_generated_at"),
        "items": items,
        "warning": (
            "CLOSE_REVIEW, OPEN_REVIEW and CANNIBALIZATION_REVIEW are human decisions. "
            "Never apply redirects/noindex/index solely from this queue."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage", type=Path, default=DEFAULT_COVERAGE)
    parser.add_argument("--cannibalization", type=Path, default=DEFAULT_CANNIBALIZATION)
    parser.add_argument("--graph", type=Path, default=DEFAULT_GRAPH)
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA)
    parser.add_argument("--opportunities", type=Path, default=DEFAULT_OPPORTUNITIES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--apply", action="store_true", help="write queue JSON only; never mutates SEO state")
    args = parser.parse_args()

    try:
        coverage = _read_json(args.coverage, required=True)
        cannibalization = _read_json(args.cannibalization, required=False)
        graph = _read_json(args.graph, required=False)
        metadata = _read_json(args.metadata, required=False)
        opportunities = _read_json(args.opportunities, required=False)
        queue = build_queue(
            coverage,
            cannibalization=cannibalization,
            graph=graph,
            metadata=metadata,
            opportunities=opportunities,
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        print(f"SEO action queue failed: {exc}", file=sys.stderr)
        return 2

    print("SEO action queue")
    print(f"  URLs: {queue['total_urls']}")
    for action, count in queue["action_counts"].items():
        print(f"  {action:24} {count}")
    print("  SAFETY: no SEO state is changed automatically.")

    if not args.apply:
        print(f"[DRY-RUN] would write: {args.out}")
        return 0
    if not args.out.parent.is_dir():
        print(f"output parent not found: {args.out.parent}", file=sys.stderr)
        return 3
    args.out.write_text(json.dumps(queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[WRITTEN FOR REVIEW] {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
