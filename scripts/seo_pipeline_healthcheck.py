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
    cannibalization = read("server-opt/gsc_cannibalization_report.py", failures)
    consolidation = read("server-opt/build_cannibalization_review.py", failures)
    architecture = read("SEO_ARCHITECTURE.md", failures)

    for rel in (
        "tests/test_build_search_evidence.py",
        "tests/test_pair_quality_audit.py",
        "tests/test_build_pair_policy.py",
        "tests/test_gsc_cannibalization_report.py",
        "tests/test_build_cannibalization_review.py",
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
            "hard_quality_fail": "consolidation planner ignores hard page-quality defects",
            "recommended_primary": "consolidation planner no longer proposes a primary URL",
            '"automatic_changes": False': "consolidation planner can apply automatic SEO changes",
            "merge/redirect only if intent is truly the same": "consolidation planner lost intent-review safety warning",
        },
        failures,
    )

    need("gsc_cannibalization_report.py" in architecture, "SEO architecture does not document cannibalization audit", failures)
    need("build_cannibalization_review.py" in architecture, "SEO architecture does not document consolidation review", failures)

    if failures:
        print("SEO pipeline healthcheck: FAIL")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print("SEO pipeline healthcheck: OK")
    print("  search evidence is collected before index decisions")
    print("  page quality blocks weak pair recommendations")
    print("  pair policy remains review-only before promotion")
    print("  GSC query/page cannibalization is measured with pagination")
    print("  consolidation recommendations never auto-redirect or auto-canonicalize")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
