from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "server-opt" / "build_search_evidence.py"
SPEC = importlib.util.spec_from_file_location("xgu_build_search_evidence", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot load search evidence module from {MODULE_PATH}")
evidence_mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = evidence_mod
SPEC.loader.exec_module(evidence_mod)


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


class BuildSearchEvidenceTests(unittest.TestCase):
    def test_canonical_url_normalizes_host_scheme_and_trailing_slash(self) -> None:
        self.assertEqual(
            evidence_mod.canonical_url("http://x-gu.ru/moskva/seo-audit-saita?utm=1#x"),
            "https://x-gu.ru/moskva/seo-audit-saita/",
        )
        self.assertEqual(evidence_mod.canonical_url("/moskva"), "https://x-gu.ru/moskva/")
        self.assertIsNone(evidence_mod.canonical_url("https://example.com/moskva/"))

    def test_merge_preserves_yandex_google_and_manual_signals(self) -> None:
        merged = evidence_mod._merge(
            ["https://x-gu.ru/moskva/", "https://x-gu.ru/tula/"],
            [
                {
                    "keys": ["https://x-gu.ru/moskva/"],
                    "impressions": 10,
                    "clicks": 2,
                    "position": 4.5,
                },
                {
                    "keys": ["https://x-gu.ru/kazan/"],
                    "impressions": 3,
                    "clicks": 0,
                    "position": 12.0,
                },
            ],
            ["https://x-gu.ru/perm/"],
            base_url="https://x-gu.ru",
        )

        self.assertEqual(len(merged), 4)
        self.assertTrue(merged["https://x-gu.ru/moskva/"].yandex_in_search)
        self.assertEqual(merged["https://x-gu.ru/moskva/"].gsc_impressions, 10)
        self.assertTrue(merged["https://x-gu.ru/perm/"].manual_protected)

        selected = evidence_mod.candidate_urls(merged, min_gsc_impressions=2, min_gsc_clicks=1)
        self.assertEqual(
            selected,
            [
                "https://x-gu.ru/kazan/",
                "https://x-gu.ru/moskva/",
                "https://x-gu.ru/perm/",
                "https://x-gu.ru/tula/",
            ],
        )

    def test_candidate_threshold_can_exclude_weak_google_only_url(self) -> None:
        merged = evidence_mod._merge(
            [],
            [
                {
                    "keys": ["https://x-gu.ru/moskva/weak/"],
                    "impressions": 1,
                    "clicks": 0,
                    "position": 70,
                }
            ],
            [],
            base_url="https://x-gu.ru",
        )
        self.assertEqual(
            evidence_mod.candidate_urls(merged, min_gsc_impressions=5, min_gsc_clicks=1),
            [],
        )

    def test_gsc_fetch_uses_start_row_pagination(self) -> None:
        requests = FakeRequests(
            [
                [{"keys": ["https://x-gu.ru/a/"]}, {"keys": ["https://x-gu.ru/b/"]}],
                [{"keys": ["https://x-gu.ru/c/"]}],
            ]
        )
        rows = evidence_mod.fetch_gsc_pages(
            requests,
            lambda: {"Authorization": "test"},
            "https://x-gu.ru/",
            days=90,
            row_limit=2,
        )
        self.assertEqual(len(rows), 3)
        self.assertEqual(requests.calls[0][2]["startRow"], 0)
        self.assertEqual(requests.calls[1][2]["startRow"], 2)

    def test_gsc_row_limit_is_bounded(self) -> None:
        with self.assertRaises(ValueError):
            evidence_mod.fetch_gsc_pages(FakeRequests([]), lambda: {}, "https://x-gu.ru/", days=90, row_limit=25001)


if __name__ == "__main__":
    unittest.main()
