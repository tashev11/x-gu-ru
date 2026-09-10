#!/usr/bin/env python3
"""Manage the indexable core of an x-gu.ru release candidate.

Dry-run by default. Apply writes only to an isolated release candidate unless an
explicit emergency override allows active current. The resulting keep-config is
stored inside the release as ``.xgu-index-keep.json`` so switching ``current``
also switches the policy atomically.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from release_safety import (  # noqa: E402
    DEFAULT_CURRENT,
    DEFAULT_RELEASES_ROOT,
    atomic_replace_text,
    mutation_target_error,
)


DEFAULT_WEB_ROOT = DEFAULT_CURRENT
DEFAULT_WHITELIST = Path("/opt/p3-app/data/whitelist.txt")
DEFAULT_POLICY = Path(os.getenv("XGU_INDEX_POLICY", "/opt/p3-app/data/index_policy.json"))
BUNDLED_BASELINE = Path(__file__).resolve().with_name("index_policy.baseline.json")
RELEASE_KEEP_FILENAME = ".xgu-index-keep.json"
BASE = "https://x-gu.ru"
CANONICAL_HOST = "x-gu.ru"
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

NOINDEX_TAG = '<meta name="robots" content="noindex, follow">'
INDEX_TAG = (
    '<meta name="robots" content="index,follow,max-image-preview:large,'
    'max-snippet:-1,max-video-preview:-1">'
)


def _dedupe(values: list[object]) -> list[str]:
    cleaned = [str(value).strip() for value in values if str(value).strip()]
    return list(dict.fromkeys(cleaned))


def _validate_slugs(values: list[str], label: str) -> None:
    invalid = [value for value in values if SLUG_RE.fullmatch(value) is None]
    if invalid:
        raise SystemExit(f"Invalid {label} slug(s): {', '.join(invalid[:10])}")


def _policy_digest(cities: list[str], services: list[str]) -> str:
    payload = json.dumps(
        {"open_cities": cities, "open_services": services},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _load_policy_file(path: Path, *, allow_example: bool = False) -> tuple[list[str], list[str]]:
    if not path.is_file():
        raise SystemExit(f"Policy file not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Policy file is invalid JSON: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("Policy root must be a JSON object")
    if payload.get("example_only") and not allow_example:
        raise SystemExit(
            f"Refusing example-only policy: {path}. Copy it to a reviewed production policy, "
            "set example_only=false, and record the review source/date."
        )
    if not payload.get("example_only"):
        if not str(payload.get("reviewed_at") or "").strip():
            raise SystemExit("Production policy must contain reviewed_at")
        if not str(payload.get("source_note") or "").strip():
            raise SystemExit("Production policy must contain source_note")

    cities = _dedupe(list(payload.get("open_cities") or []))
    services = _dedupe(list(payload.get("open_services") or []))
    if not cities or not services:
        raise SystemExit("Policy must contain non-empty open_cities and open_services")
    _validate_slugs(cities, "city")
    _validate_slugs(services, "service")
    return cities, services


def load_policy(path: Path | None, *, use_builtin: bool = False) -> tuple[list[str], list[str], str, str]:
    if use_builtin:
        cities, services = _load_policy_file(BUNDLED_BASELINE)
        source = f"bundled-emergency-baseline:{BUNDLED_BASELINE}"
        return cities, services, source, _policy_digest(cities, services)

    if path is None or not path.is_file():
        raise SystemExit(
            "Reviewed index policy is required. Provide --policy /path/to/index_policy.json "
            "or explicitly use --use-builtin-policy for emergency recovery only."
        )
    cities, services = _load_policy_file(path)
    return cities, services, str(path.resolve()), _policy_digest(cities, services)


def _canonical_whitelist_url(value: str) -> str:
    value = value.strip()
    if value.startswith("/"):
        value = BASE + value
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.netloc != CANONICAL_HOST:
        raise SystemExit(f"Whitelist URL must use canonical https://{CANONICAL_HOST}: {value}")
    if parsed.query or parsed.fragment:
        raise SystemExit(f"Whitelist URL must not contain query/fragment: {value}")

    decoded_parts = [unquote(part) for part in parsed.path.split("/") if part]
    if any(part in {".", ".."} or "/" in part or "\\" in part for part in decoded_parts):
        raise SystemExit(f"Whitelist URL contains unsafe path segment: {value}")

    path = parsed.path or "/"
    if path != "/" and not path.endswith("/"):
        path += "/"
    return BASE + path


def url_for(html: Path, web_root: Path) -> str:
    rel = html.parent.relative_to(web_root)
    if str(rel) == ".":
        return f"{BASE}/"
    return f"{BASE}/{rel.as_posix()}/"


def build_keep_urls(whitelist: Path, open_cities: list[str], open_services: list[str]) -> set[str]:
    if not whitelist.is_file():
        raise SystemExit(
            f"Whitelist not found: {whitelist}. Refusing index-core changes because protected URLs are unknown."
        )
    _validate_slugs(open_cities, "city")
    _validate_slugs(open_services, "service")

    keep: set[str] = set()
    for line_number, line in enumerate(whitelist.read_text(encoding="utf-8", errors="strict").splitlines(), start=1):
        value = line.strip()
        if not value:
            continue
        try:
            keep.add(_canonical_whitelist_url(value))
        except SystemExit as exc:
            raise SystemExit(f"Invalid whitelist line {line_number}: {exc}") from exc

    keep.add(f"{BASE}/")
    keep.add(f"{BASE}/privacy/")
    for city in open_cities:
        keep.add(f"{BASE}/{city}/")
        for service in open_services:
            keep.add(f"{BASE}/{city}/{service}/")
    return keep


def noindex_text(text: str) -> str | None:
    if 'name="robots" content="noindex' in text:
        return None
    new, count = re.subn(
        r'<meta name="robots" content="index,\s*follow[^"]*">',
        NOINDEX_TAG,
        text,
        count=1,
    )
    if count:
        return new
    new, count = re.subn(
        r'(<meta charset="[^"]+">)',
        r"\1\n    " + NOINDEX_TAG,
        text,
        count=1,
    )
    return new if count else None


def reopen_text(text: str) -> str | None:
    if 'name="robots" content="noindex' not in text:
        return None
    new, count = re.subn(
        r'<meta name="robots" content="noindex[^"]*">',
        INDEX_TAG,
        text,
        count=1,
    )
    return new if count else None


def make_plan(web_root: Path, keep: set[str]) -> tuple[list[tuple[Path, str]], int, int, int]:
    operations: list[tuple[Path, str]] = []
    scanned = kept = errors = 0
    for html in web_root.rglob("index.html"):
        scanned += 1
        try:
            text = html.read_text(encoding="utf-8", errors="strict")
            url = url_for(html, web_root)
            if url in keep:
                kept += 1
                if reopen_text(text) is not None:
                    operations.append((html, "reopen"))
            elif noindex_text(text) is not None:
                operations.append((html, "close"))
        except Exception as exc:  # noqa: BLE001
            errors += 1
            print(f"  ERROR {html}: {exc}", file=sys.stderr)
    return operations, scanned, kept, errors


def apply_page_plan(operations: list[tuple[Path, str]]) -> tuple[int, int]:
    changed = errors = 0
    for path, action in operations:
        try:
            text = path.read_text(encoding="utf-8", errors="strict")
            new_text = reopen_text(text) if action == "reopen" else noindex_text(text)
            if new_text is None:
                continue
            atomic_replace_text(path, new_text)
            changed += 1
        except Exception as exc:  # noqa: BLE001
            errors += 1
            print(f"  APPLY ERROR {path}: {exc}", file=sys.stderr)
            break
    return changed, errors


def write_release_keep_config(
    web_root: Path,
    open_cities: list[str],
    open_services: list[str],
    *,
    policy_source: str,
    policy_sha256: str,
) -> Path:
    path = web_root / RELEASE_KEEP_FILENAME
    payload = {
        "open_cities": open_cities,
        "open_services": open_services,
        "policy_source": policy_source,
        "policy_sha256": policy_sha256,
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    atomic_replace_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return path


def write_sitemap(web_root: Path, keep: set[str]) -> None:
    shard_dir = web_root / "sitemaps"
    shard_dir.mkdir(parents=True, exist_ok=True)
    shard = shard_dir / "sitemap-1.xml"

    urls = sorted(keep)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    lines.extend(f"  <url><loc>{url}</loc></url>" for url in urls)
    lines.append("</urlset>")
    atomic_replace_text(shard, "\n".join(lines) + "\n")

    index = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        f"  <sitemap><loc>{BASE}/sitemaps/sitemap-1.xml</loc></sitemap>",
        "</sitemapindex>",
    ]
    atomic_replace_text(web_root / "sitemap.xml", "\n".join(index) + "\n")
    print(f"sitemap rebuilt: {len(urls)} urls")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="apply robots/sitemap/release-policy changes")
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY, help="reviewed JSON open_cities/open_services policy")
    parser.add_argument(
        "--use-builtin-policy",
        action="store_true",
        help="explicit emergency fallback to the bundled historical baseline",
    )
    parser.add_argument("--web-root", type=Path, default=DEFAULT_WEB_ROOT)
    parser.add_argument("--current", type=Path, default=DEFAULT_CURRENT)
    parser.add_argument("--releases-root", type=Path, default=DEFAULT_RELEASES_ROOT)
    parser.add_argument(
        "--unsafe-allow-active-current",
        action="store_true",
        help="emergency override allowing --apply directly against active current",
    )
    parser.add_argument("--whitelist", type=Path, default=DEFAULT_WHITELIST)
    args = parser.parse_args()

    if not args.web_root.is_dir():
        raise SystemExit(f"Web root not found: {args.web_root}")

    open_cities, open_services, policy_source, policy_sha256 = load_policy(
        args.policy,
        use_builtin=args.use_builtin_policy,
    )
    keep = build_keep_urls(args.whitelist, open_cities, open_services)
    print(f"policy source: {policy_source}")
    print(f"policy sha256: {policy_sha256}")
    print(
        f"policy: cities={len(open_cities)} services={len(open_services)} "
        f"protected/indexable URLs={len(keep)}"
    )

    operations, scanned, kept, errors = make_plan(args.web_root, keep)
    close_count = sum(1 for _, action in operations if action == "close")
    reopen_count = sum(1 for _, action in operations if action == "reopen")
    print(
        f"plan: scanned={scanned} kept_open={kept} close={close_count} "
        f"reopen={reopen_count} errors={errors}"
    )
    if errors:
        print("Plan contains read errors; refusing apply.", file=sys.stderr)
        return 2

    if not args.apply:
        print("[DRY-RUN] No files changed. Re-run with --apply against an isolated release candidate.")
        return 0

    target_error = mutation_target_error(
        args.web_root,
        current=args.current,
        releases_root=args.releases_root,
        allow_active_current=args.unsafe_allow_active_current,
    )
    if target_error:
        print(f"Refusing apply: {target_error}", file=sys.stderr)
        return 3

    changed, apply_errors = apply_page_plan(operations)
    if apply_errors:
        print(
            "Page application failed; sitemap/policy manifest were not rewritten. "
            "Discard and rebuild this release candidate.",
            file=sys.stderr,
        )
        return 4

    try:
        keep_path = write_release_keep_config(
            args.web_root,
            open_cities,
            open_services,
            policy_source=policy_source,
            policy_sha256=policy_sha256,
        )
        write_sitemap(args.web_root, keep)
    except Exception as exc:  # noqa: BLE001
        print(
            f"Release metadata/sitemap write failed: {exc}. Discard and rebuild this candidate.",
            file=sys.stderr,
        )
        return 5

    print(
        f"[APPLIED] page_changes={changed} release_keep_config={keep_path} "
        f"sitemap_urls={len(keep)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
