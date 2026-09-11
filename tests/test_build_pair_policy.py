from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import date
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "server-opt" / "build_pair_policy.py"
SPEC = importlib.util.spec_from_file_location("xgu_build_pair_policy", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot load pair-policy builder from {MODULE_PATH}")
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


class BuildPairPolicyTests(unittest.TestCase):
    def test_builds_exact_pairs_without_cross_product(self) -> None:
        evidence = {
            "generated_at": "2026-09-10",
            "urls": [
                {
                    "url": "https://x-gu.ru/moskva/seo-audit-saita/",
                    "yandex_in_search": True,
                    "gsc_impressions": 10,
                    "gsc_clicks": 1,
                },
                {
                    "url": "https://x-gu.ru/tver/prodvizhenie-saita/",
                    "yandex_in_search": False,
                    "gsc_impressions": 8,
                    "gsc_clicks": 0,
                },
            ],
        }
        policy, review = mod.build_candidate(evidence, min_impressions=5, min_clicks=1)
        self.assertTrue(policy["example_only"])
        self.assertEqual(policy["policy_version"], 2)
        self.assertEqual(policy["open_cities"], ["moskva", "tver"])
        self.assertEqual(
            policy["open_pairs"],
            ["moskva/seo-audit-saita", "tver/prodvizhenie-saita"],
        )
        self.assertNotIn("moskva/prodvizhenie-saita", policy["open_pairs"])
        self.assertEqual(review["counts"]["candidate_pairs"], 2)

    def test_quality_hard_fail_blocks_pair_even_with_search_signal(self) -> None:
        url = "https://x-gu.ru/moskva/seo-audit-saita/"
        evidence = {
            "urls": [{"url": url, "yandex_in_search": True, "gsc_impressions": 100, "gsc_clicks": 5}]
        }
        quality = {
            "pairs": [{"url": url, "quality_state": "improve_before_index", "flags": ["thin_content"]}]
        }
        policy, review = mod.build_candidate(
            evidence,
            quality_payload=quality,
            require_quality=True,
            min_impressions=5,
            min_clicks=1,
        )
        self.assertEqual(policy["open_pairs"], [])
        self.assertEqual(review["counts"]["rejected_quality_hard_fail"], 1)
        self.assertEqual(review["rejected_pairs"][0]["rejection_reason"], "quality_hard_fail")

    def test_missing_quality_blocks_pair_when_quality_is_required(self) -> None:
        url = "https://x-gu.ru/tver/prodvizhenie-saita/"
        policy, review = mod.build_candidate(
            {"urls": [{"url": url, "gsc_impressions": 30}]},
            quality_payload={"pairs": []},
            require_quality=True,
            min_impressions=5,
            min_clicks=1,
        )
        self.assertEqual(policy["open_pairs"], [])
        self.assertEqual(review["counts"]["rejected_quality_missing"], 1)

    def test_clean_quality_allows_pair(self) -> None:
        url = "https://x-gu.ru/moskva/seo-audit-saita/"
        policy, review = mod.build_candidate(
            {"urls": [{"url": url, "gsc_impressions": 30}]},
            quality_payload={"pairs": [{"url": url, "quality_state": "clean", "flags": []}]},
            require_quality=True,
            min_impressions=5,
            min_clicks=1,
        )
        self.assertEqual(policy["open_pairs"], ["moskva/seo-audit-saita"])
        self.assertEqual(review["pairs"][0]["recommendation"], "candidate_open")

    def test_near_duplicate_is_kept_for_manual_similarity_review(self) -> None:
        url = "https://x-gu.ru/moskva/seo-audit-saita/"
        policy, review = mod.build_candidate(
            {"urls": [{"url": url, "yandex_in_search": True}]},
            quality_payload={
                "pairs": [{"url": url, "quality_state": "review_similarity", "flags": ["near_duplicate"]}]
            },
            require_quality=True,
            min_impressions=999,
            min_clicks=999,
        )
        self.assertEqual(policy["open_pairs"], ["moskva/seo-audit-saita"])
        self.assertEqual(review["pairs"][0]["recommendation"], "review_similarity")

    def test_weak_google_only_pair_is_not_selected(self) -> None:
        policy, review = mod.build_candidate(
            {
                "urls": [
                    {
                        "url": "https://x-gu.ru/moskva/weak-service/",
                        "gsc_impressions": 1,
                        "gsc_clicks": 0,
                    }
                ]
            },
            min_impressions=5,
            min_clicks=1,
        )
        self.assertEqual(policy["open_pairs"], [])
        self.assertEqual(policy["open_cities"], [])
        self.assertEqual(review["counts"]["rejected_below_threshold"], 1)

    def test_manual_or_yandex_signal_selects_pair_in_evidence_only_mode(self) -> None:
        policy, _review = mod.build_candidate(
            {
                "urls": [
                    {"url": "https://x-gu.ru/kazan/service-a/", "manual_protected": True},
                    {"url": "https://x-gu.ru/perm/service-b/", "yandex_in_search": True},
                ]
            },
            min_impressions=999,
            min_clicks=999,
        )
        self.assertEqual(policy["open_pairs"], ["kazan/service-a", "perm/service-b"])

    def test_strong_city_hub_is_kept_without_forcing_service_matrix(self) -> None:
        policy, _review = mod.build_candidate(
            {"urls": [{"url": "https://x-gu.ru/moskva/", "gsc_impressions": 100, "gsc_clicks": 5}]},
            min_impressions=5,
            min_clicks=1,
        )
        self.assertEqual(policy["open_cities"], ["moskva"])
        self.assertEqual(policy["open_pairs"], [])

    def test_foreign_or_deep_urls_do_not_become_pairs(self) -> None:
        policy, review = mod.build_candidate(
            {
                "urls": [
                    {"url": "https://example.com/a/b/", "yandex_in_search": True},
                    {"url": "https://x-gu.ru/a/b/c/", "yandex_in_search": True},
                ]
            }
        )
        self.assertEqual(policy["open_pairs"], [])
        self.assertGreaterEqual(review["counts"]["rejected_non_pair_url"], 1)

    def test_freshness_accepts_matching_recent_evidence_and_quality(self) -> None:
        evidence = {"generated_at": "2026-09-10"}
        quality = {
            "generated_at": "2026-09-11",
            "source_evidence_generated_at": "2026-09-10",
        }
        result = mod.validate_input_freshness(
            evidence,
            quality,
            max_age_days=14,
            today=date(2026, 9, 11),
        )
        self.assertEqual(result["evidence_age_days"], 1)
        self.assertEqual(result["quality_age_days"], 0)

    def test_stale_evidence_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "search evidence is stale"):
            mod.validate_input_freshness(
                {"generated_at": "2026-08-01"},
                None,
                max_age_days=14,
                today=date(2026, 9, 11),
            )

    def test_quality_from_different_evidence_snapshot_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "different search evidence snapshot"):
            mod.validate_input_freshness(
                {"generated_at": "2026-09-10"},
                {
                    "generated_at": "2026-09-11",
                    "source_evidence_generated_at": "2026-09-09",
                },
                max_age_days=14,
                today=date(2026, 9, 11),
            )


if __name__ == "__main__":
    unittest.main()
