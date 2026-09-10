#!/usr/bin/env python3
"""Build combined search evidence before changing x-gu.ru index coverage.

Historical tooling built the protected whitelist from Yandex first and audited
Google Search Console only after shrink. This tool reverses that order: collect
both search-engine signals first, then produce a reviewable candidate whitelist
and a per-URL evidence JSON.

It is dry-run by default. ``--apply`` writes candidate/evidence files only; it
never edits a release, sitemap, robots directives, or the live source whitelist.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Callable
from urllib.parse import quote, urlparse, urlunparse


DEFAULT_BASE_URL = "https://x-gu.ru"
DEFAULT_CANDIDATE = Path("/opt/p3-app/data/whitelist.candidate.txt")
DEFAULT_EVIDENCE = Path("/opt/p3-app/data/search_evidence.json")
DEFAULT_MANUAL = Path("/opt/p3-app/data/whitelist_manual.txt")


@dataclass
class UrlEvidence:
    url: str
    yandex_in_search: bool = False
    gsc_impressions: float = 0.0
    gsc_clicks: float = 0.0
    gsc_position: float | None = None
    manual_protected: bool = False


def canonical_url(value: str, base_url: str = DEFAULT_BASE_URL) -> str | None:
    """Return canonical x-gu.ru URL or None for foreign/unsafe URLs."""
    value = value.strip()
    if not value:
        return None
    base = urlparse(base_url.rstrip("/"))
    parsed = urlparse(value if "://" in value else base_url.rstrip("/") + "/" + value.lstrip("/"))
    if parsed.scheme not in {"http", "https"} or parsed.hostname != base.hostname:
        return None

    path = parsed.path or "/"
    if "/../" in f"/{path.strip('/')}/" or "/./" in f"/{path.strip('/')}/":
        return None
    if not path.startswith("/"):
        path = "/" + path
    if path != "/" and "." not in Path(path).name and not path.endswith("/"):
        path += "/"
    return urlunparse(("https", base.netloc, path, "", "", ""))


def _merge(
    yandex_urls: list[str],
    gsc_rows: list[dict],
    manual_urls: list[str],
    *,
    base_url: str,
) -> dict[str, UrlEvidence]:
    evidence: dict[str, UrlEvidence] = {}

    def item(url: str) -> UrlEvidence | None:
        canonical = canonical_url(url, base_url)
        if canonical is None:
            return None
        return evidence.setdefault(canonical, UrlEvidence(url=canonical))

    for url in yandex_urls:
        record = item(url)
        if record is not None:
            record.yandex_in_search = True

    for row in gsc_rows:
        keys = row.get("keys") or []
        if not keys:
            continue
        record = item(str(keys[0]))
        if record is None:
            continue
        record.gsc_impressions += float(row.get("impressions") or 0.0)
        record.gsc_clicks += float(row.get("clicks") or 0.0)
        pos = row.get("position")
        if pos is not None:
            # Page-dimension queries normally return one aggregate row per URL.
            # If duplicates are supplied in tests/merged exports, retain the
            # best observed position rather than averaging incompatible rows.
            pos_value = float(pos)
            record.gsc_position = pos_value if record.gsc_position is None else min(record.gsc_position, pos_value)

    for url in manual_urls:
        record = item(url)
        if record is not None:
            record.manual_protected = True

    return evidence


def candidate_urls(
    evidence: dict[str, UrlEvidence],
    *,
    min_gsc_impressions: float = 1.0,
    min_gsc_clicks: float = 1.0,
) -> list[str]:
    selected = []
    for url, record in evidence.items():
        if (
            record.yandex_in_search
            or record.manual_protected
            or record.gsc_impressions >= min_gsc_impressions
            or record.gsc_clicks >= min_gsc_clicks
        ):
            selected.append(url)
    return sorted(selected)


def _read_manual(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8", errors="strict").splitlines() if line.strip()]


def _backend_context():
    """Load private backend dependencies only when API collection is requested."""
    private_root = Path(os.getenv("XGU_PRIVATE_ROOT", "/opt/p3-app"))
    if str(private_root) not in sys.path:
        sys.path.insert(0, str(private_root))
    os.chdir(private_root)

    import requests  # noqa: PLC0415
    from app.core.config import settings  # noqa: PLC0415
    from app.services.google_search_console_service import _headers  # noqa: PLC0415
    from app.services.yandex_webmaster_service import _http_get, resolve_context  # noqa: PLC0415

    return requests, settings, _headers, _http_get, resolve_context


def fetch_yandex_urls(http_get: Callable[[str], dict], ctx) -> list[str]:
    urls: list[str] = []
    offset = 0
    while True:
        payload = http_get(
            f"/user/{ctx.user_id}/hosts/{ctx.host_id}/search-urls/in-search/samples"
            f"?offset={offset}&limit=100"
        )
        samples = payload.get("samples") or []
        for sample in samples:
            url = sample.get("url")
            if url:
                urls.append(str(url))
        count = payload.get("count")
        offset += len(samples)
        if not samples or (count is not None and offset >= int(count)):
            break
    return urls


def fetch_gsc_pages(
    requests_module,
    headers_factory: Callable[[], dict],
    site_url: str,
    *,
    days: int,
    row_limit: int = 25000,
) -> list[dict]:
    """Fetch all page rows using Search Analytics startRow pagination."""
    if days < 1:
        raise ValueError("days must be positive")
    if row_limit < 1 or row_limit > 25000:
        raise ValueError("row_limit must be between 1 and 25000")

    end = date.today()
    start = end - timedelta(days=days)
    endpoint = (
        "https://www.googleapis.com/webmasters/v3/sites/"
        f"{quote(site_url.strip(), safe='')}/searchAnalytics/query"
    )

    rows: list[dict] = []
    start_row = 0
    while True:
        body = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dimensions": ["page"],
            "rowLimit": row_limit,
            "startRow": start_row,
        }
        response = requests_module.post(endpoint, headers=headers_factory(), json=body, timeout=60)
        if not response.ok:
            raise RuntimeError(f"GSC query failed: {response.status_code} {response.text[:300]}")
        batch = (response.json() or {}).get("rows", [])
        rows.extend(batch)
        if len(batch) < row_limit:
            break
        start_row += len(batch)
    return rows


def _atomic_write(path: Path, text: str) -> None:
    if not path.parent.is_dir():
        raise FileNotFoundError(f"output parent does not exist: {path.parent}")
    if path.is_symlink() or path.parent.is_symlink():
        raise RuntimeError(f"refusing symlink output path: {path}")
    temp = path.with_name(f".{path.name}.next.{os.getpid()}")
    try:
        temp.write_text(text, encoding="utf-8")
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90, help="GSC evidence window")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--manual-whitelist", type=Path, default=DEFAULT_MANUAL)
    parser.add_argument("--candidate-out", type=Path, default=DEFAULT_CANDIDATE)
    parser.add_argument("--evidence-out", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--gsc-min-impressions", type=float, default=1.0)
    parser.add_argument("--gsc-min-clicks", type=float, default=1.0)
    parser.add_argument("--apply", action="store_true", help="write review files; does not promote source whitelist")
    args = parser.parse_args()

    if args.days < 1:
        print("--days must be positive", file=sys.stderr)
        return 2

    try:
        requests_module, settings, headers_factory, http_get, resolve_context = _backend_context()
        yandex_ctx = resolve_context()
        yandex_urls = fetch_yandex_urls(http_get, yandex_ctx)
        gsc_rows = fetch_gsc_pages(
            requests_module,
            headers_factory,
            settings.google_search_console_site_url,
            days=args.days,
        )
        manual_urls = _read_manual(args.manual_whitelist)
        evidence = _merge(yandex_urls, gsc_rows, manual_urls, base_url=args.base_url)
        selected = candidate_urls(
            evidence,
            min_gsc_impressions=args.gsc_min_impressions,
            min_gsc_clicks=args.gsc_min_clicks,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"Search evidence collection failed: {exc}", file=sys.stderr)
        return 2

    yandex_count = sum(1 for item in evidence.values() if item.yandex_in_search)
    gsc_count = sum(1 for item in evidence.values() if item.gsc_impressions > 0 or item.gsc_clicks > 0)
    manual_count = sum(1 for item in evidence.values() if item.manual_protected)
    overlap = sum(
        1
        for item in evidence.values()
        if item.yandex_in_search and (item.gsc_impressions > 0 or item.gsc_clicks > 0)
    )

    print("Search evidence candidate")
    print(f"  Yandex in-search URLs: {yandex_count}")
    print(f"  GSC URLs with signal ({args.days}d): {gsc_count}")
    print(f"  Yandex/GSC overlap: {overlap}")
    print(f"  manual protected URLs: {manual_count}")
    print(f"  candidate protected URLs: {len(selected)}")
    print("  NOTE: this is evidence for review, not an automatic index-all decision.")

    evidence_payload = {
        "generated_at": date.today().isoformat(),
        "gsc_window_days": args.days,
        "base_url": args.base_url,
        "thresholds": {
            "gsc_min_impressions": args.gsc_min_impressions,
            "gsc_min_clicks": args.gsc_min_clicks,
        },
        "counts": {
            "yandex_in_search": yandex_count,
            "gsc_with_signal": gsc_count,
            "overlap": overlap,
            "manual": manual_count,
            "candidate": len(selected),
        },
        "urls": [asdict(evidence[url]) for url in sorted(evidence)],
    }

    if not args.apply:
        print(f"[DRY-RUN] would write candidate: {args.candidate_out}")
        print(f"[DRY-RUN] would write evidence:  {args.evidence_out}")
        return 0

    try:
        _atomic_write(args.candidate_out, "".join(f"{url}\n" for url in selected))
        _atomic_write(args.evidence_out, json.dumps(evidence_payload, ensure_ascii=False, indent=2) + "\n")
    except Exception as exc:  # noqa: BLE001
        print(f"Writing evidence files failed: {exc}", file=sys.stderr)
        return 3

    print(f"[WRITTEN FOR REVIEW] {args.candidate_out}")
    print(f"[WRITTEN FOR REVIEW] {args.evidence_out}")
    print("Source /opt/p3-app/data/whitelist.txt was NOT changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
