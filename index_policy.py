"""Shared index-policy model for x-gu.ru programmatic SEO.

Policy v1 is the historical matrix model::

    open_cities × open_services

Policy v2 is pair-level::

    open_cities -> city hubs allowed to index
    open_pairs  -> exact city/service landing pages allowed to index

Whitelist URLs remain an explicit protected override outside either model.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any


SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SUPPORTED_POLICY_VERSIONS = {1, 2}


def _slug(value: object, label: str) -> str:
    result = str(value or "").strip()
    if not result or SLUG_RE.fullmatch(result) is None:
        raise ValueError(f"invalid {label} slug: {result!r}")
    return result


def _dedupe_slugs(values: object, label: str) -> list[str]:
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError(f"{label} must be a JSON array")
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        item = _slug(value, label)
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _pair(value: object) -> tuple[str, str]:
    if not isinstance(value, str):
        raise ValueError("open_pairs entries must be strings like city/service")
    parts = value.strip().strip("/").split("/")
    if len(parts) != 2:
        raise ValueError(f"invalid open_pairs entry: {value!r}")
    return _slug(parts[0], "city"), _slug(parts[1], "service")


def normalize_policy_payload(
    payload: dict[str, Any],
    *,
    require_review_metadata: bool = False,
    allow_example: bool = False,
) -> dict[str, Any]:
    """Validate and normalize a source policy or release policy manifest."""
    if not isinstance(payload, dict):
        raise ValueError("policy root must be a JSON object")

    if payload.get("example_only") and not allow_example:
        raise ValueError("example-only policy cannot be used for production")
    if require_review_metadata and not payload.get("example_only"):
        if not str(payload.get("reviewed_at") or "").strip():
            raise ValueError("production policy must contain reviewed_at")
        if not str(payload.get("source_note") or "").strip():
            raise ValueError("production policy must contain source_note")

    raw_version = payload.get("policy_version", 1)
    try:
        version = int(raw_version)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid policy_version: {raw_version!r}") from exc
    if version not in SUPPORTED_POLICY_VERSIONS:
        raise ValueError(f"unsupported policy_version={version}; supported={sorted(SUPPORTED_POLICY_VERSIONS)}")

    open_cities = _dedupe_slugs(payload.get("open_cities"), "city")
    if not open_cities:
        raise ValueError("policy must contain non-empty open_cities")

    if version == 1:
        open_services = _dedupe_slugs(payload.get("open_services"), "service")
        if not open_services:
            raise ValueError("policy v1 must contain non-empty open_services")
        open_pairs: list[tuple[str, str]] = []
        mode = "matrix"
    else:
        raw_pairs = payload.get("open_pairs")
        if raw_pairs is None:
            raw_pairs = []
        if not isinstance(raw_pairs, list):
            raise ValueError("open_pairs must be a JSON array")
        open_pairs = []
        seen_pairs: set[tuple[str, str]] = set()
        open_city_set = set(open_cities)
        for raw in raw_pairs:
            pair = _pair(raw)
            if pair[0] not in open_city_set:
                raise ValueError(
                    f"open pair {pair[0]}/{pair[1]} references city hub not present in open_cities"
                )
            if pair not in seen_pairs:
                seen_pairs.add(pair)
                open_pairs.append(pair)
        open_services = sorted({service for _, service in open_pairs})
        mode = "pairs"

    return {
        "policy_version": version,
        "policy_mode": mode,
        "open_cities": open_cities,
        "open_services": open_services,
        "open_pairs": open_pairs,
    }


def policy_digest(policy: dict[str, Any]) -> str:
    canonical: dict[str, Any] = {
        "policy_version": int(policy["policy_version"]),
        "open_cities": sorted(policy["open_cities"]),
    }
    if int(policy["policy_version"]) == 1:
        canonical["open_services"] = sorted(policy["open_services"])
    else:
        canonical["open_pairs"] = sorted(f"{city}/{service}" for city, service in policy["open_pairs"])
    raw = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def page_is_open(policy: dict[str, Any], city_slug: str, service_slug: str | None = None) -> bool:
    city = str(city_slug).strip()
    if city not in set(policy["open_cities"]):
        return False
    if service_slug is None:
        return True
    service = str(service_slug).strip()
    if int(policy["policy_version"]) == 1:
        return service in set(policy["open_services"])
    return (city, service) in set(policy["open_pairs"])


def services_for_city(policy: dict[str, Any], city_slug: str) -> list[str]:
    city = str(city_slug).strip()
    if city not in set(policy["open_cities"]):
        return []
    if int(policy["policy_version"]) == 1:
        return list(policy["open_services"])
    return [service for pair_city, service in policy["open_pairs"] if pair_city == city]


def keep_urls(policy: dict[str, Any], whitelist_urls: set[str], *, base_url: str = "https://x-gu.ru") -> set[str]:
    base = base_url.rstrip("/")
    keep = set(whitelist_urls)
    keep.add(base + "/")
    keep.add(base + "/privacy/")
    for city in policy["open_cities"]:
        keep.add(f"{base}/{city}/")
        for service in services_for_city(policy, city):
            keep.add(f"{base}/{city}/{service}/")
    return keep


def manifest_policy_fields(policy: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "policy_version": int(policy["policy_version"]),
        "policy_mode": str(policy["policy_mode"]),
        "open_cities": list(policy["open_cities"]),
    }
    if int(policy["policy_version"]) == 1:
        result["open_services"] = list(policy["open_services"])
    else:
        result["open_pairs"] = [f"{city}/{service}" for city, service in policy["open_pairs"]]
        result["open_services"] = list(policy["open_services"])
    return result
