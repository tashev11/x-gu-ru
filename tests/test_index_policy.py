from __future__ import annotations

import unittest

from index_policy import (
    keep_urls,
    manifest_policy_fields,
    normalize_policy_payload,
    page_is_open,
    policy_digest,
    services_for_city,
)


class IndexPolicyTests(unittest.TestCase):
    def test_v1_keeps_historical_matrix_semantics(self) -> None:
        policy = normalize_policy_payload(
            {
                "policy_version": 1,
                "open_cities": ["moskva", "tver"],
                "open_services": ["seo-audit-saita", "prodvizhenie-saita"],
            }
        )
        self.assertEqual(policy["policy_mode"], "matrix")
        self.assertTrue(page_is_open(policy, "moskva"))
        self.assertTrue(page_is_open(policy, "moskva", "seo-audit-saita"))
        self.assertTrue(page_is_open(policy, "tver", "prodvizhenie-saita"))
        self.assertFalse(page_is_open(policy, "kazan", "seo-audit-saita"))
        self.assertEqual(services_for_city(policy, "moskva"), ["seo-audit-saita", "prodvizhenie-saita"])

    def test_v2_opens_only_explicit_city_service_pairs(self) -> None:
        policy = normalize_policy_payload(
            {
                "policy_version": 2,
                "open_cities": ["moskva", "tver"],
                "open_pairs": [
                    "moskva/seo-audit-saita",
                    "tver/prodvizhenie-saita",
                ],
            }
        )
        self.assertEqual(policy["policy_mode"], "pairs")
        self.assertTrue(page_is_open(policy, "moskva"))
        self.assertTrue(page_is_open(policy, "moskva", "seo-audit-saita"))
        self.assertFalse(page_is_open(policy, "moskva", "prodvizhenie-saita"))
        self.assertTrue(page_is_open(policy, "tver", "prodvizhenie-saita"))
        self.assertEqual(services_for_city(policy, "moskva"), ["seo-audit-saita"])
        self.assertEqual(services_for_city(policy, "tver"), ["prodvizhenie-saita"])

    def test_v2_pair_requires_open_city_hub(self) -> None:
        with self.assertRaises(ValueError):
            normalize_policy_payload(
                {
                    "policy_version": 2,
                    "open_cities": ["moskva"],
                    "open_pairs": ["tver/seo-audit-saita"],
                }
            )

    def test_v2_keep_urls_do_not_expand_cross_product(self) -> None:
        policy = normalize_policy_payload(
            {
                "policy_version": 2,
                "open_cities": ["moskva", "tver"],
                "open_pairs": [
                    "moskva/seo-audit-saita",
                    "tver/prodvizhenie-saita",
                ],
            }
        )
        urls = keep_urls(policy, {"https://x-gu.ru/kazan/special/"})
        self.assertIn("https://x-gu.ru/moskva/seo-audit-saita/", urls)
        self.assertIn("https://x-gu.ru/tver/prodvizhenie-saita/", urls)
        self.assertNotIn("https://x-gu.ru/moskva/prodvizhenie-saita/", urls)
        self.assertNotIn("https://x-gu.ru/tver/seo-audit-saita/", urls)
        self.assertIn("https://x-gu.ru/kazan/special/", urls)

    def test_v2_manifest_contains_pairs_and_derived_service_inventory(self) -> None:
        policy = normalize_policy_payload(
            {
                "policy_version": 2,
                "open_cities": ["moskva", "tver"],
                "open_pairs": [
                    "moskva/seo-audit-saita",
                    "tver/seo-audit-saita",
                    "tver/prodvizhenie-saita",
                ],
            }
        )
        manifest = manifest_policy_fields(policy)
        self.assertEqual(manifest["policy_version"], 2)
        self.assertEqual(manifest["policy_mode"], "pairs")
        self.assertEqual(
            manifest["open_pairs"],
            ["moskva/seo-audit-saita", "tver/seo-audit-saita", "tver/prodvizhenie-saita"],
        )
        self.assertEqual(manifest["open_services"], ["prodvizhenie-saita", "seo-audit-saita"])

    def test_policy_digest_is_stable_after_duplicate_removal(self) -> None:
        first = normalize_policy_payload(
            {
                "policy_version": 2,
                "open_cities": ["moskva", "moskva"],
                "open_pairs": ["moskva/seo-audit-saita", "moskva/seo-audit-saita"],
            }
        )
        second = normalize_policy_payload(
            {
                "policy_version": 2,
                "open_cities": ["moskva"],
                "open_pairs": ["moskva/seo-audit-saita"],
            }
        )
        self.assertEqual(policy_digest(first), policy_digest(second))

    def test_unsupported_version_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            normalize_policy_payload(
                {
                    "policy_version": 3,
                    "open_cities": ["moskva"],
                    "open_pairs": [],
                }
            )


if __name__ == "__main__":
    unittest.main()
