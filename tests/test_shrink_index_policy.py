from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "server-opt" / "shrink_index.py"
SPEC = importlib.util.spec_from_file_location("xgu_shrink_index", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot load shrink-index module from {MODULE_PATH}")
shrink = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(shrink)


class ShrinkIndexPolicyTests(unittest.TestCase):
    def test_missing_policy_requires_explicit_builtin_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "missing.json"
            with self.assertRaises(SystemExit):
                shrink.load_policy(missing, use_builtin=False)

    def test_external_policy_is_deduplicated_and_hashed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            policy = Path(temp) / "policy.json"
            policy.write_text(
                json.dumps(
                    {
                        "open_cities": ["moskva", "moskva", "tver"],
                        "open_services": ["seo-audit-saita", "seo-audit-saita"],
                    }
                ),
                encoding="utf-8",
            )
            cities, services, source, digest = shrink.load_policy(policy, use_builtin=False)
            self.assertEqual(cities, ["moskva", "tver"])
            self.assertEqual(services, ["seo-audit-saita"])
            self.assertEqual(source, str(policy.resolve()))
            self.assertEqual(len(digest), 64)

    def test_example_only_policy_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            policy = Path(temp) / "policy.json"
            policy.write_text(
                json.dumps(
                    {
                        "example_only": True,
                        "open_cities": ["moskva"],
                        "open_services": ["seo-audit-saita"],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(SystemExit):
                shrink.load_policy(policy, use_builtin=False)

    def test_bundled_policy_is_explicit_and_auditable(self) -> None:
        cities, services, source, digest = shrink.load_policy(None, use_builtin=True)
        self.assertGreater(len(cities), 0)
        self.assertGreater(len(services), 0)
        self.assertTrue(source.startswith("bundled-emergency-baseline:"))
        self.assertEqual(len(digest), 64)
        self.assertTrue(shrink.BUNDLED_BASELINE.is_file())


if __name__ == "__main__":
    unittest.main()
