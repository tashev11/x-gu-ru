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


def reviewed_policy(**overrides):
    payload = {
        "policy_version": 1,
        "reviewed_at": "2026-09-10",
        "source_note": "unit-test reviewed policy",
        "open_cities": ["moskva", "moskva", "tver"],
        "open_services": ["seo-audit-saita", "seo-audit-saita"],
    }
    payload.update(overrides)
    return payload


def reviewed_v2(**overrides):
    payload = {
        "policy_version": 2,
        "reviewed_at": "2026-09-10",
        "source_note": "unit-test pair-level policy",
        "open_cities": ["moskva", "tver"],
        "open_pairs": [
            "moskva/seo-audit-saita",
            "moskva/prodvizhenie-saita",
            "tver/prodvizhenie-saita",
        ],
    }
    payload.update(overrides)
    return payload


class ShrinkIndexPolicyTests(unittest.TestCase):
    def test_missing_policy_requires_explicit_builtin_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "missing.json"
            with self.assertRaises(SystemExit):
                shrink.load_policy(missing, use_builtin=False)

    def test_external_policy_is_deduplicated_and_hashed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            policy = Path(temp) / "policy.json"
            policy.write_text(json.dumps(reviewed_policy()), encoding="utf-8")
            cities, services, source, digest = shrink.load_policy(policy, use_builtin=False)
            self.assertEqual(cities, ["moskva", "tver"])
            self.assertEqual(services, ["seo-audit-saita"])
            self.assertEqual(source, str(policy.resolve()))
            self.assertEqual(len(digest), 64)

    def test_v2_exact_pairs_do_not_expand_cross_product(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            policy_file = Path(temp) / "policy.json"
            policy_file.write_text(json.dumps(reviewed_v2()), encoding="utf-8")
            policy, source, digest = shrink.load_policy_model(policy_file, use_builtin=False)
            keep = shrink.build_keep_urls_for_policy(set(), policy)

            self.assertEqual(source, str(policy_file.resolve()))
            self.assertEqual(len(digest), 64)
            self.assertIn("https://x-gu.ru/moskva/seo-audit-saita/", keep)
            self.assertIn("https://x-gu.ru/moskva/prodvizhenie-saita/", keep)
            self.assertIn("https://x-gu.ru/tver/prodvizhenie-saita/", keep)
            self.assertNotIn("https://x-gu.ru/tver/seo-audit-saita/", keep)
            self.assertEqual(policy["policy_mode"], "pairs")

    def test_v2_pair_with_closed_city_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            policy_file = Path(temp) / "policy.json"
            policy_file.write_text(
                json.dumps(reviewed_v2(open_cities=["moskva"], open_pairs=["tver/prodvizhenie-saita"])),
                encoding="utf-8",
            )
            with self.assertRaises(SystemExit):
                shrink.load_policy_model(policy_file, use_builtin=False)

    def test_production_policy_requires_review_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            policy = Path(temp) / "policy.json"
            policy.write_text(json.dumps(reviewed_policy(reviewed_at="")), encoding="utf-8")
            with self.assertRaises(SystemExit):
                shrink.load_policy(policy, use_builtin=False)

    def test_invalid_slug_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            policy = Path(temp) / "policy.json"
            policy.write_text(json.dumps(reviewed_policy(open_cities=["moskva", "../tula"])), encoding="utf-8")
            with self.assertRaises(SystemExit):
                shrink.load_policy(policy, use_builtin=False)

    def test_example_only_policy_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            policy = Path(temp) / "policy.json"
            policy.write_text(
                json.dumps(
                    {
                        "policy_version": 2,
                        "example_only": True,
                        "open_cities": ["moskva"],
                        "open_pairs": ["moskva/seo-audit-saita"],
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

    def test_whitelist_rejects_external_domain(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            whitelist = Path(temp) / "whitelist.txt"
            whitelist.write_text("https://example.com/moskva/\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                shrink.load_whitelist_urls(whitelist)

    def test_whitelist_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            whitelist = Path(temp) / "whitelist.txt"
            whitelist.write_text("https://x-gu.ru/moskva/../admin/\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                shrink.load_whitelist_urls(whitelist)

    def test_whitelist_rejects_unsupported_depth(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            whitelist = Path(temp) / "whitelist.txt"
            whitelist.write_text("https://x-gu.ru/moskva/service/extra/\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                shrink.load_whitelist_urls(whitelist)

    def test_relative_whitelist_path_is_canonicalized(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            whitelist = Path(temp) / "whitelist.txt"
            whitelist.write_text("/moskva/seo-audit-saita\n", encoding="utf-8")
            urls = shrink.load_whitelist_urls(whitelist)
            self.assertEqual(urls, {"https://x-gu.ru/moskva/seo-audit-saita/"})
            keep = shrink.build_keep_urls_from_whitelist(urls, ["moskva"], ["seo-audit-saita"])
            self.assertIn("https://x-gu.ru/moskva/seo-audit-saita/", keep)

    def test_whitelist_snapshot_is_sorted_and_hash_is_deterministic(self) -> None:
        urls = {
            "https://x-gu.ru/tver/",
            "https://x-gu.ru/moskva/seo-audit-saita/",
        }
        expected_text = (
            "https://x-gu.ru/moskva/seo-audit-saita/\n"
            "https://x-gu.ru/tver/\n"
        )
        self.assertEqual(shrink._whitelist_snapshot_text(urls), expected_text)
        self.assertEqual(shrink.whitelist_digest(urls), shrink.whitelist_digest(set(reversed(sorted(urls)))))

    def test_release_contract_v1_is_written_inside_release(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            release = Path(temp)
            urls = {"https://x-gu.ru/moskva/"}
            whitelist_path = shrink.write_release_whitelist(release, urls)
            whitelist_sha = shrink.whitelist_digest(urls)
            keep_path = shrink.write_release_keep_config(
                release,
                ["moskva"],
                ["seo-audit-saita"],
                policy_source="/reviewed/index_policy.json",
                policy_sha256="a" * 64,
                whitelist_source="/reviewed/whitelist.txt",
                whitelist_sha256=whitelist_sha,
            )
            payload = json.loads(keep_path.read_text(encoding="utf-8"))
            self.assertEqual(whitelist_path, release / shrink.RELEASE_WHITELIST_FILENAME)
            self.assertEqual(payload["policy_version"], 1)
            self.assertEqual(payload["open_cities"], ["moskva"])
            self.assertEqual(payload["open_services"], ["seo-audit-saita"])

    def test_release_contract_v2_preserves_exact_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            release = Path(temp)
            policy = shrink.normalize_policy_payload(reviewed_v2())
            keep_path = shrink.write_release_keep_config(
                release,
                policy=policy,
                policy_source="/reviewed/index_policy.json",
                policy_sha256="a" * 64,
                whitelist_source="/reviewed/whitelist.txt",
                whitelist_sha256="b" * 64,
            )
            payload = json.loads(keep_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["policy_version"], 2)
            self.assertEqual(payload["policy_mode"], "pairs")
            self.assertEqual(payload["open_pairs"], reviewed_v2()["open_pairs"])

    def test_shared_release_guard_is_used_for_apply_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            releases = root / "releases"
            releases.mkdir()
            candidate = releases / "candidate"
            candidate.mkdir()
            active = releases / "active"
            active.mkdir()
            current = root / "current"
            current.symlink_to(active)

            self.assertIsNone(shrink.mutation_target_error(candidate, current=current, releases_root=releases))
            self.assertIsNotNone(shrink.mutation_target_error(active, current=current, releases_root=releases))
            self.assertIsNone(
                shrink.mutation_target_error(
                    active,
                    current=current,
                    releases_root=releases,
                    allow_active_current=True,
                )
            )


if __name__ == "__main__":
    unittest.main()
