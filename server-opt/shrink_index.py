#!/usr/bin/env python3
"""Manage the indexable core of an x-gu.ru release candidate.

Policy v1 keeps the historical ``open_cities × open_services`` matrix.
Policy v2 opens exact ``city/service`` pairs while city hubs remain controlled by
``open_cities``. Dry-run is the default and production writes are release-first.
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

from index_policy import (  # noqa: E402
    keep_urls as policy_keep_urls,
    manifest_policy_fields,
    normalize_policy_payload,
    policy_digest,
)
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
RELEASE_WHITELIST_FILENAME = ".xgu-whitelist.txt"
BASE = "https://x-gu.ru"
CANONICAL_HOST = "x-gu.ru"
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

NOINDEX_TAG = '<meta name="robots" content="noindex, follow">'
INDEX_TAG = (
    '<meta name="robots" content="index,follow,max-image-preview:large,'
    'max-snippet:-1,max-video-preview:-1">'
)


def _read_policy_payload(path: Path, *, allow_example: bool = False) -> dict:
    if not path.is_file():
        raise SystemExit(f"Policy file not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Policy file is invalid JSON: {path}: {exc}") from exc
    try:
        return normalize_policy_payload(
            payload,
            require_review_metadata=not bool(payload.get("example_only")),
            allow_example=allow_example,
        )
    except ValueError as exc:
        raise SystemExit(f"Invalid index policy {path}: {exc}") from exc


def load_policy_model(path: Path | None, *, use_builtin: bool = False) -> tuple[dict, str, str]:
    if use_builtin:
        policy = _read_policy_payload(BUNDLED_BASELINE)
        source = f"bundled-emergency-baseline:{BUNDLED_BASELINE}"
        return policy, source, policy_digest(policy)

    if path is None or not path.is_file():
        raise SystemExit(
            "Reviewed index policy is required. Provide --policy /path/to/index_policy.json "
            "or explicitly use --use-builtin-policy for emergency recovery only."
        )
    policy = _read_policy_payload(path)
    return policy, str(path.resolve()), policy_digest(policy)


def load_policy(path: Path | None, *, use_builtin: bool = False) -> tuple[list[str], list[str], str, str]:
    """Compatibility wrapper returning city/service inventory plus provenance."""
    policy, source, digest = load_policy_model(path, use_builtin=use_builtin)
    return list(policy["open_cities"]), list(policy["open_services"]), source, digest


def _validate_slugs(values: list[str], label: str) -> None:
    invalid = [value for value in values if SLUG_RE.fullmatch(value) is None]
    if invalid:
        raise SystemExit(f"Invalid {label} slug(s): {', '.join(invalid[:10])}")


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
    if len(decoded_parts) > 2:
        raise SystemExit(f"Whitelist URL has unsupported path depth: {value}")

    path = parsed.path or "/"
    if path != "/" and not path.endswith("/"):
        path += "/"
    return BASE + path


def load_whitelist_urls(whitelist: Path) -> set[str]:
    if not whitelist.is_file():
        raise SystemExit(
            f"Whitelist not found: {whitelist}. Refusing index-core changes because protected URLs are unknown."
        )
    urls: set[str] = set()
    try:
        lines = whitelist.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError) as exc:
        raise SystemExit(f"Cannot read whitelist: {whitelist}: {exc}") from exc
    for line_number, line in enumerate(lines, start=1):
        value = line.strip()
        if not value:
            continue
        try:
            urls.add(_canonical_whitelist_url(value))
        except SystemExit as exc:
            raise SystemExit(f"Invalid whitelist line {line_number}: {exc}") from exc
    return urls


def _whitelist_snapshot_text(urls: set[str]) -> str:
    return "".join(f"{url}\n" for url in sorted(urls))


def whitelist_digest(urls: set[str]) -> str:
    return hashlib.sha256(_whitelist_snapshot_text(urls).encode("utf-8")).hexdigest()


def url_for(html: Path, web_root: Path) -> str:
    rel = html.parent.relative_to(web_root)
    if str(rel) == ".":
        return f"{BASE}/"
    return f"{BASE}/{rel.as_posix()}/"


def build_keep_urls_for_policy(whitelist_urls: set[str], policy: dict) -> set[str]:
    return policy_keep_urls(policy, whitelist_urls, base_url=BASE)


def build_keep_urls_from_whitelist(
    whitelist_urls: set[str],
    open_cities: list[str],
    open_services: list[str],
) -> set[str]:
    """Historical v1 matrix helper retained for scripts/tests."""
    policy = normalize_policy_payload(
        {
            "policy_version": 1,
            "open_cities": open_cities,
            "open_services": open_services,
        }
    )
    return build_keep_urls_for_policy(whitelist_urls, policy)


def build_keep_urls(whitelist: Path, open_cities: list[str], open_services: list[str]) -> set[str]:
    return build_keep_urls_from_whitelist(load_whitelist_urls(whitelist), open_cities, open_services)


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


def write_release_whitelist(web_root: Path, urls: set[str]) -> Path:
    path = web_root / RELEASE_WHITELIST_FILENAME
    atomic_replace_text(path, _whitelist_snapshot_text(urls))
    return path


def write_release_keep_config(
    web_root: Path,
    open_cities: list[str] | None = None,
    open_services: list[str] | None = None,
    *,
    policy: dict | None = None,
    policy_source: str,
    policy_sha256: str,
    whitelist_source: str,
    whitelist_sha256: str,
) -> Path:
    if policy is None:
        policy = normalize_policy_payload(
            {
                "policy_version": 1,
                "open_cities": open_cities or [],
                "open_services": open_services or [],
            }
        )
    path = web_root / RELEASE_KEEP_FILENAME
    payload = {
        **manifest_policy_fields(policy),
        "policy_source": policy_source,
        "policy_sha256": policy_sha256,
        "whitelist_source": whitelist_source,
        "whitelist_sha256": whitelist_sha256,
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
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY, help="reviewed JSON index policy v1 or v2")
    parser.add_argument(
        "--use-builtin-policy",
        action="store_true",
        help="explicit emergency fallback to the bundled historical v1 baseline",
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

    policy, policy_source, policy_sha256 = load_policy_model(args.policy, use_builtin=args.use_builtin_policy)
    whitelist_urls = load_whitelist_urls(args.whitelist)
    whitelist_sha256 = whitelist_digest(whitelist_urls)
    keep = build_keep_urls_for_policy(whitelist_urls, policy)

    print(f"policy version: {policy['policy_version']} ({policy['policy_mode']})")
    print(f"policy source: {policy_source}")
    print(f"policy sha256: {policy_sha256}")
    print(f"whitelist source: {args.whitelist.resolve()}")
    print(f"whitelist sha256: {whitelist_sha256}")
    print(
        f"policy: cities={len(policy['open_cities'])} services={len(policy['open_services'])} "
        f"pairs={len(policy['open_pairs'])} whitelist_urls={len(whitelist_urls)} "
        f"protected/indexable_urls={len(keep)}"
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
            "Page application failed; release metadata/sitemap were not rewritten. "
            "Discard and rebuild this release candidate.",
            file=sys.stderr,
        )
        return 4

    try:
        whitelist_path = write_release_whitelist(args.web_root, whitelist_urls)
        keep_path = write_release_keep_config(
            args.web_root,
            policy=policy,
            policy_source=policy_source,
            policy_sha256=policy_sha256,
            whitelist_source=str(args.whitelist.resolve()),
            whitelist_sha256=whitelist_sha256,
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
        f"release_whitelist={whitelist_path} sitemap_urls={len(keep)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
