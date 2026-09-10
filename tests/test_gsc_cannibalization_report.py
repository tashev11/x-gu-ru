from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "server-opt" / "gsc_cannibalization_report.py"
SPEC = importlib.util.spec_from_file_location("xgu_gsc_cannibalization", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot load cannibalization report from {MODULE_PATH}")
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


class FakeResponse:
    def __init__(self, rows):
        self.ok = True
        self.status_code = 200
        self.text = ""
        self._rows = rows

    def json(self):
        return {"rows": self._rows}


class FakeRequests:
    def __init__(self, batches):
        self.batches = list(batches)
        self.calls = []

    def post(self, endpoint, *, headers, json, timeout):
        self.calls.append((endpoint, headers, json, timeout))
        return FakeResponse(self.batches.pop(0))


class GscCannibalizationTests(unittest.TestCase):
    def test_same_query_two_pages_in_same_city_is_reported(self) -> None:
        rows = [
            {
                "keys": ["продвижение сайта москва", "https://x-gu.ru/moskva/prodvizhenie-saita/"],
                "impressions": 100,
                "clicks": 5,
                "position": 7,
            },
            {
                "keys": ["продвижение сайта москва", "https://x-gu.ru/moskva/seo-prodvizhenie-saita/"],
                "impressions": 40,
                "clicks": 1,
                "position": 14,
            },
        ]
        report = mod.analyze_rows(rows)
        self.assertEqual(report["queries_with_multiple_pages"], 1)
        self.assertEqual(report["affected_pages"], 2)
        self.assertEqual(report["same_city_competing_pairs"], 1)
        pair = report["same_city_pairs"][0]
        self.assertEqual(pair["city"], "moskva")
        self.assertEqual(pair["shared_queries"], 1)
        self.assertEqual(pair["shared_impressions"], 140)

    def test_single_page_query_is_not_cannibalization(self) -> None:
        report = mod.analyze_rows(
            [
                {
                    "keys": ["seo аудит москва", "https://x-gu.ru/moskva/seo-audit-saita/"],
                    "impressions": 50,
                    "clicks": 2,
                    "position": 8,
                }
            ]
        )
        self.assertEqual(report["queries_with_multiple_pages"], 0)
        self.assertEqual(report["competing_page_pairs"], 0)

    def test_www_http_and_query_case_are_canonicalized(self) -> None:
        rows = [
            {
                "keys": ["SEO Аудит", "http://www.x-gu.ru/moskva/seo-audit-saita"],
                "impressions": 10,
                "clicks": 1,
                "position": 5,
            },
            {
                "keys": ["seo   аудит", "https://x-gu.ru/tver/seo-audit-saita/"],
                "impressions": 8,
                "clicks": 0,
                "position": 11,
            },
        ]
        report = mod.analyze_rows(rows)
        self.assertEqual(report["queries_with_multiple_pages"], 1)
        urls = [item["url"] for item in report["query_conflicts"][0]["pages"]]
        self.assertIn("https://x-gu.ru/moskva/seo-audit-saita/", urls)

    def test_low_impression_row_can_be_filtered(self) -> None:
        rows = [
            {"keys": ["q", "https://x-gu.ru/moskva/a/"], "impressions": 10},
            {"keys": ["q", "https://x-gu.ru/moskva/b/"], "impressions": 0.5},
        ]
        report = mod.analyze_rows(rows, min_row_impressions=1)
        self.assertEqual(report["queries_with_multiple_pages"], 0)

    def test_query_page_fetch_uses_start_row_pagination(self) -> None:
        fake = FakeRequests(
            [
                [{"keys": ["q1", "https://x-gu.ru/a/"]}, {"keys": ["q2", "https://x-gu.ru/b/"]}],
                [{"keys": ["q3", "https://x-gu.ru/c/"]}],
            ]
        )
        rows = mod.fetch_query_page_rows(
            fake,
            lambda: {"Authorization": "x"},
            "https://x-gu.ru/",
            days=90,
            row_limit=2,
            max_rows=10,
        )
        self.assertEqual(len(rows), 3)
        self.assertEqual(fake.calls[0][2]["dimensions"], ["query", "page"])
        self.assertEqual(fake.calls[0][2]["startRow"], 0)
        self.assertEqual(fake.calls[1][2]["startRow"], 2)

    def test_row_limit_above_api_max_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            mod.fetch_query_page_rows(FakeRequests([]), lambda: {}, "https://x-gu.ru/", row_limit=25001)


if __name__ == "__main__":
    unittest.main()
