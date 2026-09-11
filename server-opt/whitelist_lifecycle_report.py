#!/usr/bin/env python3
"""Review whether protected/whitelisted x-gu.ru URLs still have current evidence.

The report is read-only. It never removes a protected URL automatically because
historical links, conversions or business requirements may exist outside search
console data.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


DEFAULT_WHITELIST = Path("/var/www/x-gu.ru/current/.xgu-whitelist.txt")
DEFAULT_EVIDENCE = Path("/opt/p3-app/data/search_evidence.json")


def _read_whitelist(path: Path) -> list[str]:
    if not path.is_file():
        raise FileNotFoundError(f"whitelist not found: {path}")
    return sorted({line.strip() for line in path.read_text(encoding="utf-8", errors="strict").splitlines() if line.strip()})


def _evidence_map(payload: dict) -> dict[str, dict]:
    rows = payload.get("urls") or []
    if not isinstance(rows, list):
        raise ValueError("search evidence urls must be an array")
    result: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        if url:
            result[url] = row
    return result


def _has_current_signal(row: dict, *, min_impressions: float, min_clicks: float) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if bool(row.get("manual_protected")):
        reasons.append("manual_protected")
    if bool(row.get("yandex_in_search")):
        reasons.append("yandex_in_search")
    impressions = float(row.get("gsc_impressions") or 0.0)
    clicks = float(row.get("gsc_clicks") or 0.0)
    if impressions >= min_impressions:
        reasons.append(f"gsc_impressions>={min_impressions:g}")
    if clicks >= min_clicks:
        reasons.append(f"gsc_clicks>={min_clicks:g}")
    return bool(reasons), reasons


def build_report(
    whitelist_urls: list[str],
    evidence_payload: dict,
    *,
    min_impressions: float = 5.0,
    min_clicks: float = 1.0,
) -> dict:
    evidence = _evidence_map(evidence_payload)
    active: list[dict] = []
    stale_review: list[dict] = []
    missing_evidence: list[str] = []

    for url in whitelist_urls:
        row = evidence.get(url)
        if row is None:
            missing_evidence.append(url)
            continue
        current, reasons = _has_current_signal(
            row,
            min_impressions=min_impressions,
            min_clicks=min_clicks,
        )
        item = {
            "url": url,
            "reasons": reasons,
            "yandex_in_search": bool(row.get("yandex_in_search")),
            "gsc_impressions": float(row.get("gsc_impressions") or 0.0),
            "gsc_clicks": float(row.get("gsc_clicks") or 0.0),
            "manual_protected": bool(row.get("manual_protected")),
        }
        if current:
            active.append(item)
        else:
            stale_review.append(item)

    evidence_urls = set(evidence)
    whitelist_set = set(whitelist_urls)
    signal_outside_whitelist: list[dict] = []
    for url in sorted(evidence_urls - whitelist_set):
        row = evidence[url]
        current, reasons = _has_current_signal(
            row,
            min_impressions=min_impressions,
            min_clicks=min_clicks,
        )
        if current:
            signal_outside_whitelist.append({"url": url, "reasons": reasons})

    return {
        "thresholds": {
            "gsc_min_impressions": min_impressions,
            "gsc_min_clicks": min_clicks,
        },
        "whitelist_urls": len(whitelist_urls),
        "active_evidence_urls": len(active),
        "stale_review_urls": len(stale_review),
        "missing_evidence_urls": len(missing_evidence),
        "signal_outside_whitelist_urls": len(signal_outside_whitelist),
        "active": active,
        "stale_review": stale_review,
        "missing_evidence": missing_evidence,
        "signal_outside_whitelist": signal_outside_whitelist,
        "automatic_removals": False,
        "warning": (
            "A stale/missing-evidence whitelist URL is only a review candidate. "
            "Check backlinks, conversions, manual business requirements and historical performance before removal."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--whitelist", type=Path, default=DEFAULT_WHITELIST)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--gsc-min-impressions", type=float, default=5.0)
    parser.add_argument("--gsc-min-clicks", type=float, default=1.0)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    if args.gsc_min_impressions < 0 or args.gsc_min_clicks < 0:
        print("thresholds must be non-negative", file=sys.stderr)
        return 2
    try:
        whitelist = _read_whitelist(args.whitelist)
        evidence = json.loads(args.evidence.read_text(encoding="utf-8", errors="strict"))
        report = build_report(
            whitelist,
            evidence,
            min_impressions=args.gsc_min_impressions,
            min_clicks=args.gsc_min_clicks,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"Whitelist lifecycle report failed: {exc}", file=sys.stderr)
        return 2

    print("Whitelist lifecycle report")
    print(f"  protected URLs:              {report['whitelist_urls']}")
    print(f"  current evidence:            {report['active_evidence_urls']}")
    print(f"  stale review candidates:     {report['stale_review_urls']}")
    print(f"  missing from evidence set:   {report['missing_evidence_urls']}")
    print(f"  signals outside whitelist:   {report['signal_outside_whitelist_urls']}")
    print("  SAFETY: no protected URL is removed automatically.")

    for item in report["stale_review"][:20]:
        print(f"  REVIEW stale: {item['url']}")
    for url in report["missing_evidence"][:20]:
        print(f"  REVIEW missing evidence: {url}")

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"  JSON report: {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
