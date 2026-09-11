#!/usr/bin/env python3
"""Audit content quality for city/service URLs found in search evidence.

This audit is policy-independent: a page may currently be ``noindex`` and still
be inspected as a possible future policy-v2 pair. It never changes files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from programmatic_seo_audit import _near_duplicate_groups, _normalized_body, _simhash64  # noqa: E402
from seo_healthcheck import PageParser, _invalid_jsonld_blocks, _normalize_url  # noqa: E402


TOKEN_RE = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
DEFAULT_ROOT = Path("/var/www/x-gu.ru/current")
DEFAULT_EVIDENCE = Path("/opt/p3-app/data/search_evidence.json")
DEFAULT_OUT = Path("/opt/p3-app/data/pair_quality.json")


def _pair_parts(url: str, base_url: str) -> tuple[str, str] | None:
    parsed = urlparse(url)
    base = urlparse(base_url.rstrip("/"))
    if parsed.scheme != "https" or parsed.netloc != base.netloc or parsed.query or parsed.fragment:
        return None
    parts = [part for part in parsed.path.split("/") if part]
    return (parts[0], parts[1]) if len(parts) == 2 else None


def _file_for(root: Path, url: str, base_url: str) -> Path | None:
    pair = _pair_parts(url, base_url)
    if pair is None:
        return None
    return root / pair[0] / pair[1] / "index.html"


def _read_evidence(path: Path) -> tuple[list[dict], dict]:
    if not path.is_file():
        raise FileNotFoundError(f"search evidence not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    rows = payload.get("urls") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("search evidence must contain an 'urls' array")
    return [row for row in rows if isinstance(row, dict)], payload


def audit_pairs(
    root: Path,
    evidence_rows: list[dict],
    *,
    base_url: str = "https://x-gu.ru",
    min_words: int = 300,
    simhash_distance: int = 6,
) -> dict:
    if min_words < 1:
        raise ValueError("min_words must be positive")
    if simhash_distance < 0 or simhash_distance > 7:
        raise ValueError("simhash_distance must be between 0 and 7")

    root = root.resolve()
    records: dict[str, dict] = {}
    exact_buckets: dict[str, list[str]] = defaultdict(list)
    simhashes: dict[str, int] = {}

    for source in evidence_rows:
        raw_url = str(source.get("url") or "").strip()
        pair = _pair_parts(raw_url, base_url)
        if pair is None:
            continue
        url = _normalize_url(raw_url)
        path = _file_for(root, url, base_url)
        assert path is not None
        flags: list[str] = []
        record = {
            "url": url,
            "city": pair[0],
            "service": pair[1],
            "path": str(path),
            "exists": path.is_file(),
            "words": 0,
            "title": "",
            "description": "",
            "h1": "",
            "canonical": "",
            "invalid_jsonld_blocks": 0,
            "flags": flags,
        }
        records[url] = record

        if not path.is_file():
            flags.append("missing_page")
            continue
        try:
            html = path.read_text(encoding="utf-8", errors="strict")
        except (OSError, UnicodeError):
            flags.append("unreadable_page")
            continue

        parser = PageParser()
        try:
            parser.feed(html)
        except Exception:
            flags.append("html_parse_error")

        words = len(TOKEN_RE.findall(parser.body_text))
        title = parser.title.strip()
        description = parser.description.strip()
        h1 = next((value.strip() for value in parser.h1s if value.strip()), "")
        canonical = _normalize_url(parser.canonical) if parser.canonical else ""
        invalid_jsonld = _invalid_jsonld_blocks(html)

        record.update(
            {
                "words": words,
                "title": title,
                "description": description,
                "h1": h1,
                "canonical": canonical,
                "invalid_jsonld_blocks": invalid_jsonld,
            }
        )
        if words < min_words:
            flags.append("thin_content")
        if not title:
            flags.append("missing_title")
        if not description:
            flags.append("missing_description")
        if not h1:
            flags.append("missing_h1")
        if not canonical:
            flags.append("missing_canonical")
        elif canonical != url:
            flags.append("canonical_mismatch")
        if invalid_jsonld:
            flags.append("invalid_jsonld")

        normalized = _normalized_body(parser.body_text)
        if normalized:
            exact = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            exact_buckets[exact].append(url)
            simhashes[url] = _simhash64(normalized)

    exact_groups = [sorted(group) for group in exact_buckets.values() if len(group) > 1]
    exact_groups.sort(key=lambda group: (-len(group), group[0]))
    near_groups = _near_duplicate_groups(simhashes, simhash_distance)

    for group in exact_groups:
        for url in group:
            records[url]["flags"].append("exact_duplicate")
    for group in near_groups:
        for url in group:
            if "near_duplicate" not in records[url]["flags"]:
                records[url]["flags"].append("near_duplicate")

    hard_flags = {
        "missing_page",
        "unreadable_page",
        "html_parse_error",
        "thin_content",
        "missing_title",
        "missing_description",
        "missing_h1",
        "missing_canonical",
        "canonical_mismatch",
        "invalid_jsonld",
        "exact_duplicate",
    }
    clean = 0
    needs_improvement = 0
    similarity_review = 0
    for record in records.values():
        flags = set(record["flags"])
        if flags & hard_flags:
            record["quality_state"] = "improve_before_index"
            needs_improvement += 1
        elif "near_duplicate" in flags:
            record["quality_state"] = "review_similarity"
            similarity_review += 1
        else:
            record["quality_state"] = "clean"
            clean += 1
        record["flags"] = sorted(set(record["flags"]))

    return {
        "root": str(root),
        "base_url": base_url.rstrip("/"),
        "min_words": min_words,
        "simhash_distance": simhash_distance,
        "pairs_audited": len(records),
        "clean_pairs": clean,
        "pairs_needing_improvement": needs_improvement,
        "pairs_needing_similarity_review": similarity_review,
        "exact_duplicate_groups": exact_groups,
        "near_duplicate_groups": near_groups,
        "pairs": [records[url] for url in sorted(records)],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--min-words", type=int, default=300)
    parser.add_argument("--simhash-distance", type=int, default=6)
    args = parser.parse_args()

    if not args.root.is_dir():
        print(f"release root not found: {args.root}", file=sys.stderr)
        return 2
    try:
        rows, evidence_payload = _read_evidence(args.evidence)
        audit = audit_pairs(
            args.root,
            rows,
            min_words=args.min_words,
            simhash_distance=args.simhash_distance,
        )
        audit["generated_at"] = date.today().isoformat()
        audit["source_evidence_generated_at"] = evidence_payload.get("generated_at")
        audit["source_evidence_path"] = str(args.evidence.resolve())
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        print(f"pair quality audit failed: {exc}", file=sys.stderr)
        return 2

    print("Pair quality audit")
    print(f"  search-evidence pairs audited: {audit['pairs_audited']}")
    print(f"  clean:                         {audit['clean_pairs']}")
    print(f"  improve before index:          {audit['pairs_needing_improvement']}")
    print(f"  similarity review:             {audit['pairs_needing_similarity_review']}")
    print(f"  exact duplicate groups:        {len(audit['exact_duplicate_groups'])}")
    print(f"  near duplicate groups:         {len(audit['near_duplicate_groups'])}")
    print(f"  source evidence date:          {audit['source_evidence_generated_at']}")

    if not args.out.parent.is_dir():
        print(f"output parent not found: {args.out.parent}", file=sys.stderr)
        return 3
    args.out.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
