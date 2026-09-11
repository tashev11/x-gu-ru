from __future__ import annotations

import unittest

from index_policy import normalize_policy_payload
from seo_healthcheck import _expected_indexable


BASE = "https://x-gu.ru"


class SeoHealthcheckV2Tests(unittest.TestCase):
    def _policy(self) -> dict:
        policy = normalize_policy_payload(
            {
                "policy_version": 2,
                "open_cities": ["moskva", "tver"],
                "open_pairs": [
                    "moskva/seo-audit-saita",
                    "moskva/prodvizhenie-saita",
                    "tver/prodvizhenie-saita",
                ],
            }
        )
        return {
            **policy,
            "open_cities": set(policy["open_cities"]),
            "open_services": set(policy["open_services"]),
            "open_pairs": set(policy["open_pairs"]),
            "whitelist_urls": set(),
        }

    def test_open_city_does_not_open_every_service(self) -> None:
        policy = self._policy()
        self.assertTrue(_expected_indexable(f"{BASE}/tver/", BASE, policy))
        self.assertTrue(_expected_indexable(f"{BASE}/tver/prodvizhenie-saita/", BASE, policy))
        self.assertFalse(_expected_indexable(f"{BASE}/tver/seo-audit-saita/", BASE, policy))

    def test_whitelist_can_protect_exact_exception(self) -> None:
        policy = self._policy()
        policy["whitelist_urls"].add(f"{BASE}/tver/seo-audit-saita/")
        self.assertTrue(_expected_indexable(f"{BASE}/tver/seo-audit-saita/", BASE, policy))

    def test_closed_city_pair_stays_closed(self) -> None:
        policy = self._policy()
        self.assertFalse(_expected_indexable(f"{BASE}/kazan/prodvizhenie-saita/", BASE, policy))


if __name__ == "__main__":
    unittest.main()
