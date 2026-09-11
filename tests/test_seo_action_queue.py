from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "server-opt" / "seo_action_queue.py"
SPEC = importlib.util.spec_from_file_location("xgu_seo_action_queue", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot load SEO action queue from {MODULE_PATH}")
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


def row(url: str, cohort: str, *, service: str = "service-a", signal: bool = True, quality: str = "clean") -> dict:
    return {
        "url": url,
        "city": "moskva",
        "service": service,
        "policy_open": cohort.startswith("open_"),
        "has_current_signal": signal,
        "gsc_impressions": 20,
        "gsc_clicks": 2,
        "quality_state": quality,
        "quality_flags": [],
    }


class SeoActionQueueTests(unittest.TestCase):
    def test_coverage_cohorts_map_to_expected_primary_actions(self) -> None:
        coverage = {
            "policy_version": 2,
            "policy_mode": "pairs",
            "cohorts": {
                "open_with_signal": [row("https://x-gu.ru/moskva/a/", "open_with_signal")],
                "open_without_signal": [row("https://x-gu.ru/moskva/b/", "open_without_signal", signal=False)],
                "open_with_signal_quality_fail": [
                    row(
                        "https://x-gu.ru/moskva/c/",
                        "open_with_signal_quality_fail",
                        quality="improve_before_index",
                    )
                ],
                "closed_with_signal_quality_ready": [
                    row("https://x-gu.ru/moskva/d/", "closed_with_signal_quality_ready")
                ],
                "closed_with_signal_quality_fail": [
                    row(
                        "https://x-gu.ru/moskva/e/",
                        "closed_with_signal_quality_fail",
                        quality="improve_before_index",
                    )
                ],
            },
        }
        queue = mod.build_queue(coverage)
        actions = {item["url"]: item["primary_action"] for item in queue["items"]}
        self.assertEqual(actions["https://x-gu.ru/moskva/a/"], "KEEP")
        self.assertEqual(actions["https://x-gu.ru/moskva/b/"], "CLOSE_REVIEW")
        self.assertEqual(actions["https://x-gu.ru/moskva/c/"], "IMPROVE_OPEN_PAGE")
        self.assertEqual(actions["https://x-gu.ru/moskva/d/"], "OPEN_REVIEW")
        self.assertEqual(actions["https://x-gu.ru/moskva/e/"], "IMPROVE_BEFORE_OPEN")
        self.assertFalse(queue["automatic_changes"])

    def test_cannibalization_overrides_keep_into_manual_review(self) -> None:
        url = "https://x-gu.ru/moskva/a/"
        other = "https://x-gu.ru/moskva/b/"
        coverage = {"cohorts": {"open_with_signal": [row(url, "open_with_signal")]}}
        cannibalization = {
            "reviews": [
                {
                    "recommended_primary": url,
                    "shared_queries": 3,
                    "queries": ["q1", "q2", "q3"],
                    "recommendation_confidence": "review",
                    "candidates": [{"url": url}, {"url": other}],
                }
            ]
        }
        queue = mod.build_queue(coverage, cannibalization=cannibalization)
        item = queue["items"][0]
        self.assertEqual(item["primary_action"], "CANNIBALIZATION_REVIEW")
        self.assertIn("cannibalization", item["annotations"])

    def test_unreachable_keep_page_becomes_internal_linking_action(self) -> None:
        url = "https://x-gu.ru/moskva/a/"
        coverage = {"cohorts": {"open_with_signal": [row(url, "open_with_signal")]}}
        graph = {"unreachable_from_home_urls": [url], "deep_indexable_urls": []}
        queue = mod.build_queue(coverage, graph=graph)
        item = queue["items"][0]
        self.assertEqual(item["primary_action"], "INTERNAL_LINKING")
        self.assertIn("INTERNAL_LINKING", item["secondary_actions"])

    def test_cross_service_metadata_overlap_becomes_intent_review(self) -> None:
        url = "https://x-gu.ru/moskva/a/"
        other = "https://x-gu.ru/moskva/b/"
        coverage = {"cohorts": {"open_with_signal": [row(url, "open_with_signal")]}}
        metadata = {
            "cross_service_examples": [
                {
                    "left": url,
                    "right": other,
                    "combined": 0.91,
                    "service_left": "a",
                    "service_right": "b",
                }
            ],
            "service_stats": [],
        }
        queue = mod.build_queue(coverage, metadata=metadata)
        item = queue["items"][0]
        self.assertEqual(item["primary_action"], "INTENT_REVIEW")
        self.assertIn("INTENT_REVIEW", item["secondary_actions"])

    def test_gsc_snippet_opportunity_can_promote_keep_page(self) -> None:
        url = "https://x-gu.ru/moskva/a/"
        coverage = {"cohorts": {"open_with_signal": [row(url, "open_with_signal")]}}
        opportunities = {
            "opportunities": [
                {
                    "url": url,
                    "category": "SNIPPET_REVIEW",
                    "position": 6.2,
                    "impressions": 500,
                    "ctr": 0.008,
                }
            ]
        }
        queue = mod.build_queue(coverage, opportunities=opportunities)
        item = queue["items"][0]
        self.assertEqual(item["primary_action"], "SNIPPET_REVIEW")
        self.assertIn("SNIPPET_REVIEW", item["secondary_actions"])
        self.assertIn("gsc_opportunity", item["annotations"])

    def test_closed_gsc_signal_without_evidence_becomes_consistency_review(self) -> None:
        url = "https://x-gu.ru/moskva/a/"
        coverage = {
            "cohorts": {
                "closed_without_signal": [row(url, "closed_without_signal", signal=False)]
            }
        }
        opportunities = {
            "opportunities": [
                {
                    "url": url,
                    "category": "CLOSED_SIGNAL_REVIEW",
                    "position": 16,
                    "impressions": 80,
                    "ctr": 0.01,
                }
            ]
        }
        queue = mod.build_queue(coverage, opportunities=opportunities)
        item = queue["items"][0]
        self.assertEqual(item["primary_action"], "EVIDENCE_MISMATCH_REVIEW")
        self.assertIn("OPEN_REVIEW", item["secondary_actions"])
        self.assertTrue(item["annotations"]["coverage_opportunity_consistency_review"])

    def test_items_are_sorted_by_action_priority_then_search_signal(self) -> None:
        coverage = {
            "cohorts": {
                "open_with_signal": [row("https://x-gu.ru/moskva/keep/", "open_with_signal")],
                "open_with_signal_quality_fail": [
                    row(
                        "https://x-gu.ru/moskva/fix/",
                        "open_with_signal_quality_fail",
                        quality="improve_before_index",
                    )
                ],
            }
        }
        queue = mod.build_queue(coverage)
        self.assertEqual(queue["items"][0]["primary_action"], "IMPROVE_OPEN_PAGE")


if __name__ == "__main__":
    unittest.main()
