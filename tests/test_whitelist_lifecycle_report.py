from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "server-opt" / "whitelist_lifecycle_report.py"
SPEC = importlib.util.spec_from_file_location("xgu_whitelist_lifecycle", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot load whitelist lifecycle report from {MODULE_PATH}")
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


class WhitelistLifecycleTests(unittest.TestCase):
    def test_classifies_current_stale_and_missing_evidence(self) -> None:
        whitelist = [
            "https://x-gu.ru/moskva/a/",
            "https://x-gu.ru/moskva/b/",
            "https://x-gu.ru/moskva/c/",
        ]
        evidence = {
            "urls": [
                {
                    "url": "https://x-gu.ru/moskva/a/",
                    "gsc_impressions": 10,
                    "gsc_clicks": 0,
                },
                {
                    "url": "https://x-gu.ru/moskva/b/",
                    "gsc_impressions": 0,
                    "gsc_clicks": 0,
                    "yandex_in_search": False,
                },
            ]
        }
        report = mod.build_report(whitelist, evidence, min_impressions=5, min_clicks=1)
        self.assertEqual(report["active_evidence_urls"], 1)
        self.assertEqual(report["stale_review_urls"], 1)
        self.assertEqual(report["missing_evidence_urls"], 1)
        self.assertFalse(report["automatic_removals"])

    def test_manual_protected_url_remains_active_without_search_signal(self) -> None:
        url = "https://x-gu.ru/tver/special/"
        report = mod.build_report(
            [url],
            {"urls": [{"url": url, "manual_protected": True}]},
            min_impressions=999,
            min_clicks=999,
        )
        self.assertEqual(report["active_evidence_urls"], 1)
        self.assertIn("manual_protected", report["active"][0]["reasons"])

    def test_signal_outside_whitelist_is_reported(self) -> None:
        url = "https://x-gu.ru/kazan/new-service/"
        report = mod.build_report(
            [],
            {"urls": [{"url": url, "yandex_in_search": True}]},
        )
        self.assertEqual(report["signal_outside_whitelist_urls"], 1)
        self.assertEqual(report["signal_outside_whitelist"][0]["url"], url)


if __name__ == "__main__":
    unittest.main()
