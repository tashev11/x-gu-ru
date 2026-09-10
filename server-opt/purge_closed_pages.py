#!/usr/bin/env python3
"""Physically remove closed pages from an isolated release candidate.

A directory is eligible only when all safeguards agree:
1. its URL is absent from every release sitemap URL and release whitelist;
2. its own ``index.html`` exists;
3. that ``index.html`` explicitly contains ``noindex``.

The release whitelist hash must match the release manifest. Dry-run is default;
active-current deletion needs an explicit emergency override.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from html import unescape
from pathlib import Path
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from release_safety import DEFAULT_CURRENT, DEFAULT_RELEASES_ROOT, mutation_target_error  # noqa: E402


DEFAULT_WEB_ROOT = DEFAULT_CURRENT
KEEP_FILENAME = ".xgu-index-keep.json"
WHITELIST_FILENAME = ".xgu-whitelist.txt"
BASE = "https://x-gu.ru"
CANONICAL_HOST = "x-gu.ru"
PROTECTED_TOP_LEVEL = {"sitemaps", "privacy", ".well-known", "assets"}
LOC_RE = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.I | re.S)


def _valid_sha256(value: object) -> bool:
    digest = str(value or "").strip().lower()
    return len(digest) == 64 and all(char in "0123456789abcdef" for char in digest)


def validate_release_contract(web_root: Path) -> tuple[Path, list[str]]:
    root = web_root.resolve()
    manifest = root / KEEP_FILENAME
    whitelist = root / WHITELIST_FILENAME
    errors: list[str] = []
    if not manifest.is_file():
        errors.append(f"release policy manifest missing: {manifest}")
    if not whitelist.is_file():
        errors.append(f"release whitelist snapshot missing: {whitelist}")
    if errors:
        return whitelist, errors

    try:
        payload = json.loads(manifest.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return whitelist, [f"release policy manifest is invalid JSON: {exc}"]
    if not isinstance(payload, dict):
        return whitelist, ["release policy manifest root must be a JSON object"]

    expected = str(payload.get("whitelist_sha256") or "").strip().lower()
    if not str(payload.get("whitelist_source") or "").strip():
        errors.append("release policy manifest has no whitelist_source")
    if not _valid_sha256(expected):
        errors.append("release policy manifest has invalid whitelist_sha256")
    elif whitelist.is_file():
        actual = hashlib.sha256(whitelist.read_bytes()).hexdigest()
        if actual != expected:
            errors.append(f"release whitelist SHA-256 mismatch: manifest={expected} actual={actual}")
    return whitelist, errors


def _canonical_page_url(value: str) -> str:
    value = unescape(value.strip())
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.netloc != CANONICAL_HOST:
        raise ValueError(f"non-canonical protected URL: {value}")
    if parsed.query or parsed.fragment:
        raise ValueError(f"protected URL contains query/fragment: {value}")
    path = parsed.path or "/"
    if path != "/" and not path.endswith("/"):
        path += "/"
    return BASE + path


def load_protected_urls(web_root: Path, whitelist: Path) -> set[str]:
    sitemap_files = sorted(web_root.rglob("sitemap*.xml"))
    if not sitemap_files:
        raise RuntimeError(f"no sitemap files found under release: {web_root}")
    if not whitelist.is_file():
        raise RuntimeError(f"release whitelist missing: {whitelist}")

    urls: set[str] = set()
    for sitemap in sitemap_files:
        text = sitemap.read_text(encoding="utf-8", errors="strict")
        for raw in LOC_RE.findall(text):
            parsed = urlparse(unescape(raw.strip()))
            if parsed.scheme == "https" and parsed.netloc == CANONICAL_HOST and parsed.path.endswith(".xml"):
                continue
            urls.add(_canonical_page_url(raw))

    for line_number, line in enumerate(whitelist.read_text(encoding="utf-8", errors="strict").splitlines(), start=1):
        value = line.strip()
        if not value:
            continue
        try:
            urls.add(_canonical_page_url(value))
        except ValueError as exc:
            raise RuntimeError(f"invalid release whitelist line {line_number}: {exc}") from exc
    return urls


def _explicit_noindex(index_file: Path) -> bool:
    if not index_file.is_file():
        return False
    head = index_file.read_text(encoding="utf-8", errors="strict")[:8000].lower()
    return re.search(r'<meta\b[^>]*name=["\']robots["\'][^>]*content=["\'][^"\']*noindex', head, re.I) is not None


def dir_size(path: Path) -> int:
    total = 0
    for file in path.rglob("*"):
        if file.is_file():
            try:
                total += file.stat().st_size
            except OSError:
                pass
    return total


def build_plan(web_root: Path, protected: set[str]) -> tuple[list[tuple[Path, str]], dict[str, int]]:
    to_delete: list[tuple[Path, str]] = []
    stats = {"kept": 0, "missing_index": 0, "not_noindex": 0}

    for city_dir in sorted(web_root.iterdir()):
        if not city_dir.is_dir() or city_dir.name in PROTECTED_TOP_LEVEL or city_dir.name.startswith("."):
            continue
        city = city_dir.name

        for sub in sorted(city_dir.iterdir()):
            if not sub.is_dir() or sub.is_symlink():
                continue
            url = f"{BASE}/{city}/{sub.name}/"
            if url in protected:
                stats["kept"] += 1
                continue

            index_file = sub / "index.html"
            if not index_file.is_file():
                stats["missing_index"] += 1
                continue
            if not _explicit_noindex(index_file):
                stats["not_noindex"] += 1
                continue
            to_delete.append((sub, url))

        hub_url = f"{BASE}/{city}/"
        if hub_url in protected:
            stats["kept"] += 1
            continue

        city_has_open = any(
            f"{BASE}/{city}/{sub.name}/" in protected
            for sub in city_dir.iterdir()
            if sub.is_dir() and not sub.is_symlink()
        )
        hub_file = city_dir / "index.html"
        if city_has_open:
            continue
        if not hub_file.is_file():
            stats["missing_index"] += 1
            continue
        if not _explicit_noindex(hub_file):
            stats["not_noindex"] += 1
            continue
        to_delete.append((city_dir, hub_url))

    return to_delete, stats


def _safe_delete(path: Path, web_root: Path) -> None:
    root = web_root.resolve()
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise RuntimeError(f"refusing path outside release root: {resolved}") from exc
    if not relative.parts or relative.parts[0] in PROTECTED_TOP_LEVEL:
        raise RuntimeError(f"refusing protected/root path: {resolved}")
    if path.is_symlink():
        raise RuntimeError(f"refusing symlink deletion: {path}")
    shutil.rmtree(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="delete planned closed-page directories")
    parser.add_argument("--root", type=Path, default=DEFAULT_WEB_ROOT)
    parser.add_argument("--current", type=Path, default=DEFAULT_CURRENT)
    parser.add_argument("--releases-root", type=Path, default=DEFAULT_RELEASES_ROOT)
    parser.add_argument(
        "--unsafe-allow-active-current",
        action="store_true",
        help="emergency override allowing deletion directly from active current",
    )
    args = parser.parse_args()

    if not args.root.is_dir():
        print(f"Release root not found: {args.root}", file=sys.stderr)
        return 1

    whitelist, contract_errors = validate_release_contract(args.root)
    if contract_errors:
        for error in contract_errors:
            print(f"  ERROR: {error}", file=sys.stderr)
        return 2

    if args.apply:
        target_error = mutation_target_error(
            args.root,
            current=args.current,
            releases_root=args.releases_root,
            allow_active_current=args.unsafe_allow_active_current,
        )
        if target_error:
            print(f"Refusing apply before scan/delete: {target_error}", file=sys.stderr)
            return 3

    try:
        protected = load_protected_urls(args.root, whitelist)
        to_delete, stats = build_plan(args.root, protected)
    except (OSError, UnicodeError, RuntimeError, ValueError) as exc:
        print(f"Purge planning failed: {exc}", file=sys.stderr)
        return 2

    print(f"protected URLs (sitemap + release whitelist): {len(protected)}")
    print(
        f"plan: delete={len(to_delete)} kept={stats['kept']} "
        f"missing_index_skipped={stats['missing_index']} not_noindex_skipped={stats['not_noindex']}"
    )
    for _path, url in to_delete[:8]:
        print(f"  DELETE {url}")
    if len(to_delete) > 8:
        print(f"  ... and {len(to_delete) - 8} more")

    if not args.apply:
        print("[DRY-RUN] Nothing deleted. Re-run against this isolated release candidate with --apply after review.")
        return 0

    freed = deleted = 0
    for path, url in to_delete:
        # A candidate may have become current after planning started. Re-check
        # before every destructive delete rather than trusting a stale plan.
        target_error = mutation_target_error(
            args.root,
            current=args.current,
            releases_root=args.releases_root,
            allow_active_current=args.unsafe_allow_active_current,
        )
        if target_error:
            print(f"Purge stopped before {url}: {target_error}", file=sys.stderr)
            return 4
        try:
            freed += dir_size(path)
            _safe_delete(path, args.root)
            deleted += 1
        except Exception as exc:  # noqa: BLE001
            print(
                f"Delete failed for {path}: {exc}. Discard/rebuild this release candidate.",
                file=sys.stderr,
            )
            return 5

    print(f"[APPLIED] deleted_directories={deleted} freed_mb={freed / 1024 / 1024:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
