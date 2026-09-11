#!/usr/bin/env python3
"""Review exact city/service index coverage against current search evidence.

The report is read-only. It classifies every physical city/service page by the
current release policy, current search evidence and (when available) page
quality. It never changes robots, sitemap, whitelist or production policy.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse


SERVER_OPT = Path(__file__).resolve().parent
REPO_ROOT = SERVER_OPT.parent
for path in (str(SERVER_OPT), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from seo_healthcheck import (  # noqa: E402
    _default_keep_config,
    _default_whitelist,
    _expected_indexable,
    _load_index_policy,
    _normalize_base_url,
    _normalize_url,
    _page_url,
)


DEFAULT_ROOT = Path("/var/www/x-gu.ru/current")
DEFAULT_EVIDENCE = Path("/opt/p3-app/data/search_evidence.json")
DEFAULT_QUALITY = Path("/opt/p3-app/data/pair_quality.json")


def _pair_parts(url: str) -> tuple[str, str] | None:
    parts = [part for part in urlparse(url).path.split("/") if part]
    return (parts[0], parts[1]) if len(parts) == 2 else None


def _read_json(path: Path, label: str) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label} root must be a JSON object")
    return payload


def _evidence_map(payload: dict) -> dict[str, dict]:
    rows = payload.get("urls") or []
    if not isinstance(rows, list):
        raise ValueError("search evidence urls must be an array")
    result: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = _normalize_url(str(row.get("url") or ""))
        if url:
            result[url] = row
    return result


def _quality_map(payload: dict | None) -> dict[str, dict]:
    if payload is None:
        return {}
    rows = payload.get("pairs") or payload.get("pages") or []
    if not isinstance(rows, list):
        raise ValueError("pair quality pairs/pages must be an array")
    result: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = _normalize_url(str(row.get("url") or ""))
        if url:
            result[url] = row
    return result


def _signal(row: dict | None, *, min_impressions: float, min_clicks: float) -> tuple[bool, list[str]]:
    if row is None:
        return False, []
    reasons: list[str] = []
    if bool(row.get("manual_protected")):
        reasons.append("manual")
    if bool(row.get("yandex_in_search")):
        reasons.append("yandex")
    impressions = float(row.get("gsc_impressions") or 0.0)
    clicks = float(row.get("gsc_clicks") or 0.0)
    if impressions >= min_impressions:
        reasons.append(f"gsc_impressions>={min_impressions:g}")
    if clicks >= min_clicks:
        reasons.append(f"gsc_clicks>={min_clicks:g}")
    return bool(reasons), reasons


def _quality_state(row: dict | None) -> str:
    if row is None:
        return "missing_quality"
    return str(row.get("quality_state") or row.get("status") or "unknown")


def build_review(
    root: Path,
    evidence_payload: dict,
    *,
    quality_payload: dict | None = None,
    base_url: str = "https://x-gu.ru",
    min_impressions: float = 5.0,
    min_clicks: float = 1.0,
) -> dict:
    root = root.resolve()
    base_url = _normalize_base_url(base_url)
    keep = _default_keep_config(root).resolve()
    whitelist = _default_whitelist(root, keep).resolve()
    policy = _load_index_policy(keep, whitelist, base_url)
    if policy is None:
        raise RuntimeError("release policy could not be loaded")

    evidence = _evidence_map(evidence_payload)
    quality = _quality_map(quality_payload)
    cohorts: dict[str, list[dict]] = defaultdict(list)
    service_counts: dict[str, Counter[str]] = defaultdict(Counter)
    city_counts: dict[str, Counter[str]] = defaultdict(Counter)

    pair_pages = 0
    for html in sorted(root.rglob("index.html")):
        url = _normalize_url(_page_url(root, html, base_url))
        pair = _pair_parts(url)
        if pair is None:
            continue
        pair_pages += 1
        expected_open = bool(_expected_indexable(url, base_url, policy))
        evidence_row = evidence.get(url)
        has_signal, signal_reasons = _signal(
            evidence_row,
            min_impressions=min_impressions,
            min_clicks=min_clicks,
        )
        quality_row = quality.get(url)
        quality_state = _quality_state(quality_row)
        quality_hard_fail = quality_state == "improve_before_index"

        if expected_open and has_signal and quality_hard_fail:
            cohort = "open_with_signal_quality_fail"
        elif expected_open and has_signal:
            cohort = "open_with_signal"
        elif expected_open:
            cohort = "open_without_signal"
        elif has_signal and quality_hard_fail:
            cohort = "closed_with_signal_quality_fail"
        elif has_signal and quality_state in {"clean", "review_similarity"}:
            cohort = "closed_with_signal_quality_ready"
        elif has_signal:
            cohort = "closed_with_signal_quality_unknown"
        else:
            cohort = "closed_without_signal"

        row = {
            "url": url,
            "city": pair[0],
            "service": pair[1],
            "policy_open": expected_open,
            "has_current_signal": has_signal,
            "signal_reasons": signal_reasons,
            "gsc_impressions": float((evidence_row or {}).get("gsc_impressions") or 0.0),
            "gsc_clicks": float((evidence_row or {}).get("gsc_clicks") or 0.0),
            "yandex_in_search": bool((evidence_row or {}).get("yandex_in_search")),
            "manual_protected": bool((evidence_row or {}).get("manual_protected")),
            "quality_state": quality_state,
            "quality_flags": list((quality_row or {}).get("flags") or []),
        }
        cohorts[cohort].append(row)
        service_counts[pair[1]][cohort] += 1
        city_counts[pair[0]][cohort] += 1

    for rows in cohorts.values():
        rows.sort(key=lambda row: (-row["gsc_clicks"], -row["gsc_impressions"], row["url"]))

    service_summary = []
    for service, counts in service_counts.items():
        open_total = sum(counts[key] for key in counts if key.startswith("open_"))
        open_supported = counts["open_with_signal"] + counts["open_with_signal_quality_fail"]
        service_summary.append(
            {
                "service": service,
                "physical_pairs": sum(counts.values()),
                "open_pairs": open_total,
                "open_with_signal": open_supported,
                "open_without_signal": counts["open_without_signal"],
                "closed_with_signal_quality_ready": counts["closed_with_signal_quality_ready"],
                "open_signal_coverage_ratio": round(open_supported / open_total, 4) if open_total else None,
            }
        )
    service_summary.sort(
        key=lambda row: (
            -(row["open_without_signal"]),
            -(row["closed_with_signal_quality_ready"]),
            row["service"],
        )
    )

    city_summary = []
    for city, counts in city_counts.items():
        open_total = sum(counts[key] for key in counts if key.startswith("open_"))
        city_summary.append(
            {
                "city": city,
                "physical_pairs": sum(counts.values()),
                "open_pairs": open_total,
                "open_without_signal": counts["open_without_signal"],
                "closed_with_signal_quality_ready": counts["closed_with_signal_quality_ready"],
            }
        )
    city_summary.sort(
        key=lambda row: (-row["open_without_signal"], -row["closed_with_signal_quality_ready"], row["city"])
    )

    counts = {name: len(rows) for name, rows in cohorts.items()}
    open_pairs = sum(value for name, value in counts.items() if name.startswith("open_"))
    signal_open = counts.get("open_with_signal", 0) + counts.get("open_with_signal_quality_fail", 0)
    return {
        "root": str(root),
        "base_url": base_url,
        "policy_version": int(policy["policy_version"]),
        "policy_mode": str(policy["policy_mode"]),
        "evidence_generated_at": evidence_payload.get("generated_at"),
        "quality_generated_at": (quality_payload or {}).get("generated_at"),
        "thresholds": {
            "gsc_min_impressions": min_impressions,
            "gsc_min_clicks": min_clicks,
        },
        "physical_pair_pages": pair_pages,
        "policy_open_pairs_on_disk": open_pairs,
        "policy_closed_pairs_on_disk": pair_pages - open_pairs,
        "open_pairs_with_current_signal": signal_open,
        "open_pair_signal_coverage_ratio": round(signal_open / open_pairs, 4) if open_pairs else None,
        "cohort_counts": counts,
        "cohorts": dict(sorted(cohorts.items())),
        "service_summary": service_summary[:200],
        "city_summary": city_summary[:300],
        "automatic_policy_changes": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--quality", type=Path, default=DEFAULT_QUALITY)
    parser.add_argument("--base-url", default="https://x-gu.ru")
    parser.add_argument("--gsc-min-impressions", type=float, default=5.0)
    parser.add_argument("--gsc-min-clicks", type=float, default=1.0)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    if not args.root.is_dir():
        print(f"release root not found: {args.root}", file=sys.stderr)
        return 2
    if args.gsc_min_impressions < 0 or args.gsc_min_clicks < 0:
        print("thresholds must be non-negative", file=sys.stderr)
        return 2
    try:
        evidence = _read_json(args.evidence, "search evidence")
        quality = _read_json(args.quality, "pair quality") if args.quality.is_file() else None
        review = build_review(
            args.root,
            evidence,
            quality_payload=quality,
            base_url=args.base_url,
            min_impressions=args.gsc_min_impressions,
            min_clicks=args.gsc_min_clicks,
        )
    except (OSError, UnicodeError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"Index coverage review failed: {exc}", file=sys.stderr)
        return 2

    counts = review["cohort_counts"]
    print("Exact index coverage review")
    print(f"  physical city/service pages:         {review['physical_pair_pages']}")
    print(f"  policy open pairs on disk:           {review['policy_open_pairs_on_disk']}")
    print(f"  policy closed pairs on disk:         {review['policy_closed_pairs_on_disk']}")
    print(f"  open pairs with current signal:      {review['open_pairs_with_current_signal']}")
    print(f"  open signal coverage ratio:          {review['open_pair_signal_coverage_ratio']}")
    print(f"  OPEN but no current signal:          {counts.get('open_without_signal', 0)}")
    print(f"  OPEN with signal but quality fail:   {counts.get('open_with_signal_quality_fail', 0)}")
    print(f"  CLOSED + signal + quality ready:     {counts.get('closed_with_signal_quality_ready', 0)}")
    print(f"  CLOSED + signal + quality fail:      {counts.get('closed_with_signal_quality_fail', 0)}")
    print("  SAFETY: this report does not change production policy.")

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"  JSON report: {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
