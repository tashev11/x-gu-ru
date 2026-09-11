#!/usr/bin/env python3
"""Measure metadata templating and intent-overlap risk across indexable x-gu.ru pages.

This is intentionally read-only. High similarity is a review signal, not an
automatic reason to noindex or merge a URL.
"""
from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from statistics import median
from urllib.parse import urlparse


SERVER_OPT = Path(__file__).resolve().parent
REPO_ROOT = SERVER_OPT.parent
for path in (str(SERVER_OPT), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from programmatic_seo_audit import collect_pages  # noqa: E402
from seo_healthcheck import PageParser  # noqa: E402


SPACE_RE = re.compile(r"\s+")
PUNCT_RE = re.compile(r"[^0-9a-zа-яё]+", re.IGNORECASE)
NUMBER_RE = re.compile(r"\b\d+(?:[.,]\d+)?\b")


@dataclass(frozen=True)
class MetaRecord:
    url: str
    city: str
    service: str
    title: str
    description: str
    h1: str


def _parts(url: str) -> tuple[str, str] | None:
    parts = [part for part in urlparse(url).path.split("/") if part]
    return (parts[0], parts[1]) if len(parts) == 2 else None


def _normalize(value: str) -> str:
    value = NUMBER_RE.sub("#", value.casefold())
    value = PUNCT_RE.sub(" ", value)
    return SPACE_RE.sub(" ", value).strip()


def _ratio(left: str, right: str) -> float:
    left_n = _normalize(left)
    right_n = _normalize(right)
    if not left_n or not right_n:
        return 0.0
    return SequenceMatcher(None, left_n, right_n, autojunk=False).ratio()


def metadata_similarity(left: MetaRecord, right: MetaRecord) -> dict[str, float]:
    title = _ratio(left.title, right.title)
    h1 = _ratio(left.h1, right.h1)
    description = _ratio(left.description, right.description)
    present = [score for score, value_left, value_right in (
        (title, left.title, right.title),
        (h1, left.h1, right.h1),
        (description, left.description, right.description),
    ) if value_left.strip() and value_right.strip()]
    combined = sum(present) / len(present) if present else 0.0
    return {
        "combined": round(combined, 4),
        "title": round(title, 4),
        "h1": round(h1, 4),
        "description": round(description, 4),
    }


def collect_metadata(root: Path, base_url: str) -> list[MetaRecord]:
    records, _policy = collect_pages(root, base_url)
    result: list[MetaRecord] = []
    for page in records:
        if not page.indexable:
            continue
        pair = _parts(page.url)
        if pair is None:
            continue
        html = page.path.read_text(encoding="utf-8", errors="ignore")
        parser = PageParser()
        try:
            parser.feed(html)
        except Exception:
            pass
        h1 = next((value.strip() for value in parser.h1s if value.strip()), "")
        result.append(
            MetaRecord(
                url=page.url,
                city=pair[0],
                service=pair[1],
                title=parser.title.strip(),
                description=parser.description.strip(),
                h1=h1,
            )
        )
    return result


def _pair_row(left: MetaRecord, right: MetaRecord, scores: dict[str, float]) -> dict:
    return {
        "left": left.url,
        "right": right.url,
        "city_left": left.city,
        "city_right": right.city,
        "service_left": left.service,
        "service_right": right.service,
        **scores,
    }


def run_audit(
    root: Path,
    *,
    base_url: str = "https://x-gu.ru",
    same_service_threshold: float = 0.88,
    cross_service_threshold: float = 0.82,
    service_template_ratio: float = 0.60,
) -> dict:
    for name, value in (
        ("same_service_threshold", same_service_threshold),
        ("cross_service_threshold", cross_service_threshold),
        ("service_template_ratio", service_template_ratio),
    ):
        if not 0 <= value <= 1:
            raise ValueError(f"{name} must be between 0 and 1")

    records = collect_metadata(root, base_url)
    by_service: dict[str, list[MetaRecord]] = defaultdict(list)
    by_city: dict[str, list[MetaRecord]] = defaultdict(list)
    for record in records:
        by_service[record.service].append(record)
        by_city[record.city].append(record)

    same_service_pairs: list[dict] = []
    service_stats: list[dict] = []
    for service, pages in sorted(by_service.items()):
        all_scores: list[float] = []
        flagged = 0
        total = 0
        for left, right in itertools.combinations(sorted(pages, key=lambda item: item.url), 2):
            total += 1
            scores = metadata_similarity(left, right)
            all_scores.append(scores["combined"])
            if scores["combined"] >= same_service_threshold:
                flagged += 1
                same_service_pairs.append(_pair_row(left, right, scores))
        ratio = flagged / total if total else 0.0
        service_stats.append(
            {
                "service": service,
                "pages": len(pages),
                "comparisons": total,
                "high_similarity_pairs": flagged,
                "high_similarity_ratio": round(ratio, 4),
                "median_similarity": round(median(all_scores), 4) if all_scores else None,
                "template_risk": len(pages) >= 3 and total > 0 and ratio >= service_template_ratio,
            }
        )

    cross_service_pairs: list[dict] = []
    for city, pages in sorted(by_city.items()):
        for left, right in itertools.combinations(sorted(pages, key=lambda item: item.url), 2):
            if left.service == right.service:
                continue
            scores = metadata_similarity(left, right)
            if scores["combined"] >= cross_service_threshold:
                row = _pair_row(left, right, scores)
                row["city"] = city
                cross_service_pairs.append(row)

    same_service_pairs.sort(key=lambda row: (-row["combined"], row["left"], row["right"]))
    cross_service_pairs.sort(key=lambda row: (-row["combined"], row["city"], row["left"], row["right"]))
    service_stats.sort(
        key=lambda row: (
            not row["template_risk"],
            -row["high_similarity_ratio"],
            -(row["pages"]),
            row["service"],
        )
    )

    return {
        "root": str(root.resolve()),
        "base_url": base_url.rstrip("/"),
        "indexable_service_pages": len(records),
        "services": len(by_service),
        "cities": len(by_city),
        "same_service_threshold": same_service_threshold,
        "cross_service_threshold": cross_service_threshold,
        "service_template_ratio": service_template_ratio,
        "same_service_high_similarity_pairs": len(same_service_pairs),
        "same_city_cross_service_high_similarity_pairs": len(cross_service_pairs),
        "services_with_template_risk": sum(1 for row in service_stats if row["template_risk"]),
        "service_stats": service_stats[:200],
        "same_service_examples": same_service_pairs[:300],
        "cross_service_examples": cross_service_pairs[:300],
    }


def evaluate(
    audit: dict,
    *,
    max_services_with_template_risk: int | None = None,
    max_cross_service_pairs: int | None = None,
) -> list[str]:
    breaches: list[str] = []
    if (
        max_services_with_template_risk is not None
        and int(audit.get("services_with_template_risk", 0)) > max_services_with_template_risk
    ):
        breaches.append(
            "services_with_template_risk="
            f"{audit['services_with_template_risk']} > {max_services_with_template_risk}"
        )
    if (
        max_cross_service_pairs is not None
        and int(audit.get("same_city_cross_service_high_similarity_pairs", 0)) > max_cross_service_pairs
    ):
        breaches.append(
            "same_city_cross_service_high_similarity_pairs="
            f"{audit['same_city_cross_service_high_similarity_pairs']} > {max_cross_service_pairs}"
        )
    return breaches


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/var/www/x-gu.ru/current"))
    parser.add_argument("--base-url", default="https://x-gu.ru")
    parser.add_argument("--same-service-threshold", type=float, default=0.88)
    parser.add_argument("--cross-service-threshold", type=float, default=0.82)
    parser.add_argument("--service-template-ratio", type=float, default=0.60)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--max-services-with-template-risk", type=int, default=None)
    parser.add_argument("--max-cross-service-pairs", type=int, default=None)
    args = parser.parse_args()

    if not args.root.is_dir():
        print(f"Root not found: {args.root}", file=sys.stderr)
        return 2
    try:
        audit = run_audit(
            args.root,
            base_url=args.base_url,
            same_service_threshold=args.same_service_threshold,
            cross_service_threshold=args.cross_service_threshold,
            service_template_ratio=args.service_template_ratio,
        )
    except (OSError, UnicodeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Metadata/intent audit failed: {exc}", file=sys.stderr)
        return 2

    print("Metadata + intent similarity audit")
    print(f"  indexable service pages:                {audit['indexable_service_pages']}")
    print(f"  services:                               {audit['services']}")
    print(f"  cities:                                 {audit['cities']}")
    print(f"  services with template risk:            {audit['services_with_template_risk']}")
    print(f"  same-service high-similarity pairs:     {audit['same_service_high_similarity_pairs']}")
    print(f"  same-city cross-service similar pairs:  {audit['same_city_cross_service_high_similarity_pairs']}")
    if audit["service_stats"]:
        print("  strongest service template risks:")
        for row in [item for item in audit["service_stats"] if item["template_risk"]][:10]:
            print(
                f"    {row['service']}: pages={row['pages']} "
                f"high_similarity={row['high_similarity_pairs']}/{row['comparisons']} "
                f"median={row['median_similarity']}"
            )

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"  JSON report: {args.json_out}")

    breaches = evaluate(
        audit,
        max_services_with_template_risk=args.max_services_with_template_risk,
        max_cross_service_pairs=args.max_cross_service_pairs,
    )
    if args.strict and breaches:
        print("Strict metadata/intent gate: FAIL", file=sys.stderr)
        for breach in breaches:
            print(f"  - {breach}", file=sys.stderr)
        return 3
    if args.strict:
        print("Strict metadata/intent gate: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
