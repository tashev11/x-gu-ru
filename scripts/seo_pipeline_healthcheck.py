#!/usr/bin/env python3
"""Static invariants for the x-gu.ru programmatic-SEO decision pipeline."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(rel: str, failures: list[str]) -> str:
    path = ROOT / rel
    if not path.is_file():
        failures.append(f"missing SEO pipeline file: {rel}")
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        failures.append(f"cannot read {rel}: {exc}")
        return ""


def need(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def tokens(text: str, required: dict[str, str], failures: list[str]) -> None:
    for token, message in required.items():
        need(token in text, message, failures)


def main() -> int:
    failures: list[str] = []
    evidence = read("server-opt/build_search_evidence.py", failures)
    quality = read("server-opt/pair_quality_audit.py", failures)
    policy = read("server-opt/build_pair_policy.py", failures)
    coverage = read("server-opt/index_coverage_review.py", failures)
    cannibalization = read("server-opt/gsc_cannibalization_report.py", failures)
    consolidation = read("server-opt/build_cannibalization_review.py", failures)
    graph = read("server-opt/link_graph_cluster_audit.py", failures)
    metadata = read("server-opt/metadata_intent_audit.py", failures)
    whitelist_lifecycle = read("server-opt/whitelist_lifecycle_report.py", failures)
    architecture = read("SEO_ARCHITECTURE.md", failures)

    for rel in (
        "tests/test_build_search_evidence.py",
        "tests/test_pair_quality_audit.py",
        "tests/test_build_pair_policy.py",
        "tests/test_index_coverage_review.py",
        "tests/test_gsc_cannibalization_report.py",
        "tests/test_build_cannibalization_review.py",
        "tests/test_link_graph_cluster_audit.py",
        "tests/test_metadata_intent_audit.py",
        "tests/test_whitelist_lifecycle_report.py",
    ):
        read(rel, failures)

    tokens(
        evidence,
        {
            "fetch_yandex_urls": "search evidence lost Yandex collection",
            "fetch_gsc_pages": "search evidence lost Google Search Console collection",
            '"startRow"': "GSC page evidence lost pagination",
            "Source /opt/p3-app/data/whitelist.txt was NOT changed": "search evidence can silently promote production whitelist",
        },
        failures,
    )
    tokens(
        quality,
        {
            "thin_content": "pair quality audit lost thin-content check",
            "canonical_mismatch": "pair quality audit lost canonical check",
            "invalid_jsonld": "pair quality audit lost JSON-LD check",
            "exact_duplicate": "pair quality audit lost exact-duplicate check",
            "near_duplicate": "pair quality audit lost near-duplicate review signal",
            "improve_before_index": "pair quality audit lost hard quality status",
            "source_evidence_generated_at": "pair quality report lost search-evidence provenance",
            '"generated_at"': "pair quality report no longer records its generation date",
        },
        failures,
    )
    tokens(
        policy,
        {
            '"policy_version": 2': "pair policy builder no longer emits v2",
            '"example_only": True': "pair policy candidate can be promoted without review",
            "require_quality": "pair policy builder no longer requires quality evidence",
            "quality_hard_fail": "hard quality defects no longer block pair recommendation",
            "review_similarity": "similarity review state is no longer preserved",
            "validate_input_freshness": "pair policy builder no longer checks evidence freshness",
            "--max-input-age-days": "pair policy builder has no explicit evidence-age limit",
            "different search evidence snapshot": "quality/evidence snapshot mismatch is no longer rejected",
        },
        failures,
    )
    tokens(
        coverage,
        {
            "open_without_signal": "coverage review no longer finds open pairs without current signal",
            "closed_with_signal_quality_ready": "coverage review no longer finds expansion candidates",
            "open_with_signal_quality_fail": "coverage review no longer finds weak currently-open pairs",
            "open_pair_signal_coverage_ratio": "coverage review no longer reports policy support ratio",
            '"automatic_policy_changes": False': "coverage review can mutate policy automatically",
        },
        failures,
    )
    tokens(
        cannibalization,
        {
            '"dimensions": ["query", "page"]': "cannibalization report no longer uses query+page GSC dimensions",
            '"startRow"': "cannibalization GSC query lost pagination",
            "same_city_competing_pairs": "same-city cannibalization is not reported",
            "data_limit_note": "Search Console top-row limitation is no longer disclosed",
        },
        failures,
    )
    tokens(
        consolidation,
        {
            "_query_metric_index": "consolidation planner no longer reconstructs real query/page metrics",
            "quality_state": "consolidation planner is not aligned with pair-quality schema",
            "hard_quality_fail": "consolidation planner ignores hard page-quality defects",
            "recommended_primary": "consolidation planner no longer proposes a primary URL",
            '"automatic_changes": False': "consolidation planner can apply automatic SEO changes",
            "merge/redirect only if intent is truly the same": "consolidation planner lost intent-review safety warning",
        },
        failures,
    )
    tokens(
        graph,
        {
            "unreachable_from_home_pages": "link audit no longer detects pages unreachable from home",
            "max_crawl_depth": "link audit no longer measures crawl depth",
            "crawl_depth_distribution": "link audit no longer reports depth distribution",
            "same_service_near_duplicate_pages": "service-cluster audit no longer detects cross-city template similarity",
            "same_service_exact_duplicate_pages": "service-cluster audit no longer detects exact same-service duplicates",
        },
        failures,
    )
    tokens(
        metadata,
        {
            "same_service_high_similarity_pairs": "metadata audit no longer measures same-service city templating",
            "same_city_cross_service_high_similarity_pairs": "metadata audit no longer measures cross-service intent overlap",
            "services_with_template_risk": "metadata audit no longer identifies service-wide template risk",
            "metadata_similarity": "metadata audit lost field-level title/H1/description similarity",
            "template_risk": "metadata audit lost service-level risk classification",
        },
        failures,
    )
    tokens(
        whitelist_lifecycle,
        {
            "stale_review_urls": "whitelist lifecycle no longer identifies stale protected URLs",
            "missing_evidence_urls": "whitelist lifecycle no longer finds protected URLs absent from current evidence",
            "signal_outside_whitelist_urls": "whitelist lifecycle no longer finds current signals outside protection",
            '"automatic_removals": False': "whitelist lifecycle can remove protected URLs automatically",
        },
        failures,
    )

    for tool in (
        "index_coverage_review.py",
        "gsc_cannibalization_report.py",
        "build_cannibalization_review.py",
        "link_graph_cluster_audit.py",
        "metadata_intent_audit.py",
        "whitelist_lifecycle_report.py",
    ):
        need(tool in architecture, f"SEO architecture does not document {tool}", failures)

    if failures:
        print("SEO pipeline healthcheck: FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("SEO pipeline healthcheck: OK")
    print("  search evidence is collected before index decisions")
    print("  evidence/quality freshness and provenance are enforced")
    print("  exact policy coverage is compared with current demand + quality")
    print("  page quality blocks weak pair recommendations")
    print("  pair policy remains review-only before promotion")
    print("  GSC query/page cannibalization is measured with pagination")
    print("  consolidation recommendations never auto-redirect or auto-canonicalize")
    print("  crawl depth and same-service cross-city similarity are measured")
    print("  metadata templating and cross-service intent overlap are measured")
    print("  protected URLs are periodically reviewable without automatic removal")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
