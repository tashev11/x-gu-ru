from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "server-opt" / "gsc_opportunity_report.py"
SPEC = importlib.util.spec_from_file_location("xgu_gsc_opportunities", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot load GSC opportunity report from {MODULE_PATH}")
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)

BASE = "https://x-gu.ru"


def gsc_row(query: str, page: str, impressions: float, clicks: float, position: float) -> dict:
    return {
        "keys": [query, page],
        "impressions": impressions,
        "clicks": clicks,
        "position": position,
    }


class GscOpportunityReportTests(unittest.TestCase):
    def test_low_ctr_top10_page_is_snippet_review(self) -> None:
        url = f"{BASE}/moskva/service-a/"
        report = mod.analyze_rows(
            [gsc_row("query", url, 100, 1, 5)],
            policy_resolver=lambda _url: True,
            min_impressions=20,
            max_snippet_ctr=0.02,
        )
        item = report["opportunities"][0]
        self.assertEqual(item["category"], "SNIPPET_REVIEW")
        self.assertEqual(item["ctr"], 0.01)

    def test_good_ctr_top_page_is_strong_not_content_growth(self) -> None:
        url = f"{BASE}/moskva/service-a/"
        report = mod.analyze_rows(
            [gsc_row("query", url, 100, 8, 4)],
            policy_resolver=lambda _url: True,
        )
        self.assertEqual(report["opportunities"][0]["category"], "STRONG_PAGE")

    def test_position_12_is_striking_distance(self) -> None:
        url = f"{BASE}/moskva/service-a/"
        report = mod.analyze_rows(
            [gsc_row("query", url, 80, 3, 12)],
            policy_resolver=lambda _url: True,
        )
        self.assertEqual(report["opportunities"][0]["category"], "STRIKING_DISTANCE")

    def test_position_25_is_content_growth(self) -> None:
        url = f"{BASE}/moskva/service-a/"
        report = mod.analyze_rows(
            [gsc_row("query", url, 80, 1, 25)],
            policy_resolver=lambda _url: True,
        )
        self.assertEqual(report["opportunities"][0]["category"], "CONTENT_GROWTH")

    def test_closed_page_with_signal_overrides_ranking_category(self) -> None:
        url = f"{BASE}/tver/service-a/"
        report = mod.analyze_rows(
            [gsc_row("query", url, 200, 10, 4)],
            policy_resolver=lambda _url: False,
        )
        self.assertEqual(report["opportunities"][0]["category"], "CLOSED_SIGNAL_REVIEW")

    def test_multiple_queries_are_aggregated_with_weighted_position(self) -> None:
        url = f"{BASE}/moskva/service-a/"
        report = mod.analyze_rows(
            [
                gsc_row("q1", url, 80, 4, 10),
                gsc_row("q2", url, 20, 1, 20),
            ],
            policy_resolver=lambda _url: True,
        )
        item = report["opportunities"][0]
        self.assertEqual(item["impressions"], 100)
        self.assertEqual(item["clicks"], 5)
        self.assertEqual(item["position"], 12.0)
        self.assertEqual(len(item["top_queries"]), 2)

    def test_below_impression_threshold_is_excluded(self) -> None:
        url = f"{BASE}/moskva/service-a/"
        report = mod.analyze_rows(
            [gsc_row("query", url, 5, 0, 8)],
            policy_resolver=lambda _url: True,
            min_impressions=20,
        )
        self.assertEqual(report["opportunities"], [])


if __name__ == "__main__":
    unittest.main()
