from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "server-opt" / "build_cannibalization_review.py"
SPEC = importlib.util.spec_from_file_location("xgu_build_cannibalization_review", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot load cannibalization review builder from {MODULE_PATH}")
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


class BuildCannibalizationReviewTests(unittest.TestCase):
    def _cannibalization(self) -> dict:
        return {
            "query_conflicts": [
                {
                    "query": "продвижение сайта москва",
                    "pages": [
                        {
                            "url": "https://x-gu.ru/moskva/prodvizhenie-saita/",
                            "impressions": 100,
                            "clicks": 5,
                            "position": 6,
                        },
                        {
                            "url": "https://x-gu.ru/moskva/seo-prodvizhenie-saita/",
                            "impressions": 40,
                            "clicks": 1,
                            "position": 11,
                        },
                    ],
                },
                {
                    "query": "seo продвижение москва",
                    "pages": [
                        {
                            "url": "https://x-gu.ru/moskva/prodvizhenie-saita/",
                            "impressions": 20,
                            "clicks": 1,
                            "position": 8,
                        },
                        {
                            "url": "https://x-gu.ru/moskva/seo-prodvizhenie-saita/",
                            "impressions": 30,
                            "clicks": 0,
                            "position": 9,
                        },
                    ],
                },
            ],
            "same_city_pairs": [
                {
                    "city": "moskva",
                    "pages": [
                        "https://x-gu.ru/moskva/prodvizhenie-saita/",
                        "https://x-gu.ru/moskva/seo-prodvizhenie-saita/",
                    ],
                    "shared_queries": 2,
                    "shared_impressions": 190,
                    "queries": ["продвижение сайта москва", "seo продвижение москва"],
                }
            ],
        }

    def test_search_winner_is_recommended_when_both_pages_are_clean(self) -> None:
        quality = {
            "pages": [
                {"url": "https://x-gu.ru/moskva/prodvizhenie-saita/", "status": "ready_for_review"},
                {"url": "https://x-gu.ru/moskva/seo-prodvizhenie-saita/", "status": "ready_for_review"},
            ]
        }
        review = mod.build_review(self._cannibalization(), quality)
        item = review["reviews"][0]
        self.assertEqual(item["recommended_primary"], "https://x-gu.ru/moskva/prodvizhenie-saita/")
        winner = item["candidates"][0]
        self.assertEqual(winner["clicks"], 6)
        self.assertEqual(winner["impressions"], 120)
        self.assertEqual(winner["metric_source"], "query_conflicts")

    def test_clean_page_beats_hard_failing_page_even_with_less_search_signal(self) -> None:
        quality = {
            "pages": [
                {
                    "url": "https://x-gu.ru/moskva/prodvizhenie-saita/",
                    "status": "improve_before_index",
                    "hard_failures": ["exact_duplicate"],
                },
                {"url": "https://x-gu.ru/moskva/seo-prodvizhenie-saita/", "status": "ready_for_review"},
            ]
        }
        review = mod.build_review(self._cannibalization(), quality)
        item = review["reviews"][0]
        self.assertEqual(item["recommended_primary"], "https://x-gu.ru/moskva/seo-prodvizhenie-saita/")
        self.assertFalse(item["candidates"][0]["hard_quality_fail"])

    def test_missing_quality_keeps_recommendation_low_confidence(self) -> None:
        review = mod.build_review(self._cannibalization(), {"pages": []})
        self.assertEqual(review["reviews"][0]["recommendation_confidence"], "fix-first")
        self.assertFalse(review["automatic_changes"])

    def test_empty_pairs_are_safe(self) -> None:
        review = mod.build_review({"query_conflicts": [], "same_city_pairs": []}, {"pages": []})
        self.assertEqual(review["review_pairs"], 0)
        self.assertEqual(review["reviews"], [])


if __name__ == "__main__":
    unittest.main()
