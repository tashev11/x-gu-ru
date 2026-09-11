from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "server-opt" / "index_coverage_review.py"
SPEC = importlib.util.spec_from_file_location("xgu_index_coverage_review", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot load index coverage review from {MODULE_PATH}")
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)

BASE = "https://x-gu.ru"


def html(url: str) -> str:
    return f"""<!doctype html><html lang="ru"><head>
<title>Тестовая страница</title>
<meta name="description" content="Описание тестовой страницы достаточной длины">
<link rel="canonical" href="{url}">
</head><body><h1>Тестовая страница</h1></body></html>"""


class IndexCoverageReviewTests(unittest.TestCase):
    def _release(self) -> tuple[tempfile.TemporaryDirectory, Path]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / "index.html").write_text(html(f"{BASE}/"), encoding="utf-8")
        for city, service in (
            ("moskva", "service-a"),
            ("tver", "service-a"),
            ("moskva", "service-b"),
            ("tver", "service-b"),
        ):
            path = root / city / service
            path.mkdir(parents=True)
            url = f"{BASE}/{city}/{service}/"
            (path / "index.html").write_text(html(url), encoding="utf-8")

        whitelist = root / ".xgu-whitelist.txt"
        whitelist.write_text("", encoding="utf-8")
        (root / ".xgu-index-keep.json").write_text(
            json.dumps(
                {
                    "policy_version": 2,
                    "policy_mode": "pairs",
                    "open_cities": ["moskva", "tver"],
                    "open_pairs": ["moskva/service-a", "tver/service-a"],
                    "open_services": ["service-a"],
                    "policy_source": "test",
                    "policy_sha256": "a" * 64,
                    "whitelist_source": "test",
                    "whitelist_sha256": hashlib.sha256(b"").hexdigest(),
                }
            ),
            encoding="utf-8",
        )
        return temp, root

    def test_exact_policy_and_signal_cohorts_are_separated(self) -> None:
        temp, root = self._release()
        self.addCleanup(temp.cleanup)
        evidence = {
            "generated_at": "2026-09-11",
            "urls": [
                {"url": f"{BASE}/moskva/service-a/", "gsc_impressions": 20},
                {"url": f"{BASE}/moskva/service-b/", "gsc_impressions": 30},
                {"url": f"{BASE}/tver/service-b/", "yandex_in_search": True},
            ],
        }
        quality = {
            "generated_at": "2026-09-11",
            "pairs": [
                {"url": f"{BASE}/moskva/service-a/", "quality_state": "clean", "flags": []},
                {"url": f"{BASE}/moskva/service-b/", "quality_state": "clean", "flags": []},
                {
                    "url": f"{BASE}/tver/service-b/",
                    "quality_state": "improve_before_index",
                    "flags": ["thin_content"],
                },
            ],
        }
        review = mod.build_review(root, evidence, quality_payload=quality, min_impressions=5, min_clicks=1)
        counts = review["cohort_counts"]
        self.assertEqual(review["policy_version"], 2)
        self.assertEqual(review["policy_open_pairs_on_disk"], 2)
        self.assertEqual(counts["open_with_signal"], 1)
        self.assertEqual(counts["open_without_signal"], 1)
        self.assertEqual(counts["closed_with_signal_quality_ready"], 1)
        self.assertEqual(counts["closed_with_signal_quality_fail"], 1)
        self.assertEqual(review["open_pair_signal_coverage_ratio"], 0.5)
        self.assertFalse(review["automatic_policy_changes"])

    def test_manual_signal_counts_as_current_support(self) -> None:
        temp, root = self._release()
        self.addCleanup(temp.cleanup)
        evidence = {
            "urls": [{"url": f"{BASE}/moskva/service-a/", "manual_protected": True}]
        }
        review = mod.build_review(root, evidence, min_impressions=999, min_clicks=999)
        self.assertEqual(review["cohort_counts"]["open_with_signal"], 1)

    def test_service_summary_exposes_open_pages_without_signal(self) -> None:
        temp, root = self._release()
        self.addCleanup(temp.cleanup)
        review = mod.build_review(root, {"urls": []})
        row = next(item for item in review["service_summary"] if item["service"] == "service-a")
        self.assertEqual(row["open_pairs"], 2)
        self.assertEqual(row["open_without_signal"], 2)
        self.assertEqual(row["open_with_signal"], 0)


if __name__ == "__main__":
    unittest.main()
