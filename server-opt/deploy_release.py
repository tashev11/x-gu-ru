#!/usr/bin/env python3
"""Validate and atomically switch x-gu.ru to a release directory.

Dry-run by default. A production release must be a direct child of the releases
root, contain its own index-policy manifest, pass structural validation and pass
the strict offline pre-deploy SEO/policy gate before ``current`` is switched.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse


SERVER_OPT = Path(__file__).resolve().parent
REPO_ROOT = SERVER_OPT.parent
for path in (str(SERVER_OPT), str(REPO_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from predeploy_check import run_predeploy  # noqa: E402


DEFAULT_RELEASES_ROOT = Path("/var/www/x-gu.ru/releases")
DEFAULT_CURRENT = Path("/var/www/x-gu.ru/current")
DEFAULT_WHITELIST = Path("/opt/p3-app/data/whitelist.txt")
KEEP_FILENAME = ".xgu-index-keep.json"
CANONICAL_HOST = "x-gu.ru"


def _required_paths(release: Path) -> list[Path]:
    return [
        release / "index.html",
        release / "robots.txt",
        release / "sitemap.xml",
        release / KEEP_FILENAME,
    ]


def _validate_release_location(release: Path, releases_root: Path) -> list[str]:
    release_resolved = release.resolve()
    root_resolved = releases_root.resolve()
    if release_resolved.parent != root_resolved:
        return [f"release must be a direct child of releases root: release={release_resolved} root={root_resolved}"]
    if release_resolved == root_resolved:
        return ["releases root itself cannot be deployed as a release"]
    return []


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _validate_sitemap_index(release: Path, sitemap: Path) -> list[str]:
    errors: list[str] = []
    try:
        root = ET.fromstring(sitemap.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, ET.ParseError) as exc:
        return [f"sitemap.xml is not valid XML: {exc}"]

    root_name = _local_name(root.tag)
    if root_name == "urlset":
        return errors
    if root_name != "sitemapindex":
        return ["sitemap.xml root must be sitemapindex or urlset"]

    locs = [
        node.text.strip()
        for node in root.iter()
        if _local_name(node.tag) == "loc" and node.text and node.text.strip()
    ]
    if not locs:
        return ["sitemap index contains no shard locations"]

    for loc in locs:
        parsed = urlparse(loc)
        if parsed.scheme != "https" or not parsed.netloc:
            errors.append(f"sitemap shard loc must be canonical HTTPS: {loc}")
            continue
        if parsed.hostname != CANONICAL_HOST:
            errors.append(f"sitemap shard loc uses non-canonical host: {loc}")
            continue
        rel = parsed.path.lstrip("/")
        shard = release / rel
        try:
            shard.relative_to(release)
        except ValueError:
            errors.append(f"sitemap shard escapes release root: {loc}")
            continue
        if not shard.is_file():
            errors.append(f"referenced sitemap shard missing: {rel}")
        elif shard.stat().st_size == 0:
            errors.append(f"referenced sitemap shard is empty: {rel}")
    return errors


def _validate_keep_manifest(path: Path) -> list[str]:
    if not path.is_file():
        return [f"required release file missing: {KEEP_FILENAME}"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [f"{KEEP_FILENAME} is invalid JSON: {exc}"]
    if not isinstance(payload, dict):
        return [f"{KEEP_FILENAME} root must be a JSON object"]

    errors: list[str] = []
    if not payload.get("open_cities"):
        errors.append(f"{KEEP_FILENAME} has no open_cities")
    if not payload.get("open_services"):
        errors.append(f"{KEEP_FILENAME} has no open_services")
    if not str(payload.get("policy_source") or "").strip():
        errors.append(f"{KEEP_FILENAME} has no policy_source")
    digest = str(payload.get("policy_sha256") or "").strip().lower()
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        errors.append(f"{KEEP_FILENAME} has invalid policy_sha256")
    return errors


def validate_release(release: Path, *, releases_root: Path | None = None) -> list[str]:
    errors: list[str] = []
    if not release.is_dir():
        return [f"release directory not found: {release}"]

    if releases_root is not None:
        errors.extend(_validate_release_location(release, releases_root))

    for path in _required_paths(release):
        if not path.is_file():
            errors.append(f"required release file missing: {path.name}")
        elif path.stat().st_size == 0:
            errors.append(f"required release file is empty: {path.name}")

    index = release / "index.html"
    if index.is_file():
        text = index.read_text(encoding="utf-8", errors="ignore")
        if "<html" not in text.lower():
            errors.append("index.html does not look like HTML")
        if "<title" not in text.lower():
            errors.append("index.html has no title")

    sitemap = release / "sitemap.xml"
    if sitemap.is_file() and sitemap.stat().st_size > 0:
        errors.extend(_validate_sitemap_index(release, sitemap))

    keep_manifest = release / KEEP_FILENAME
    if keep_manifest.is_file():
        errors.extend(_validate_keep_manifest(keep_manifest))

    return list(dict.fromkeys(errors))


def _current_target(current: Path) -> Path | None:
    if not current.exists() and not current.is_symlink():
        return None
    if not current.is_symlink():
        raise RuntimeError(f"{current} is not a symlink; refusing to replace a real directory/file atomically")
    raw = Path(os.readlink(current))
    return raw.resolve() if raw.is_absolute() else (current.parent / raw).resolve()


def switch_release(current: Path, release: Path, *, releases_root: Path | None = None) -> Path | None:
    if releases_root is not None:
        location_errors = _validate_release_location(release, releases_root)
        if location_errors:
            raise RuntimeError(location_errors[0])

    previous = _current_target(current)
    current.parent.mkdir(parents=True, exist_ok=True)
    temp_link = current.parent / f".{current.name}.next.{os.getpid()}.{time.time_ns()}"
    try:
        os.symlink(str(release.resolve()), temp_link)
        os.replace(temp_link, current)
    finally:
        if temp_link.is_symlink():
            temp_link.unlink()
    return previous


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("release_dir", type=Path, help="fully built release directory")
    parser.add_argument("--releases-root", type=Path, default=DEFAULT_RELEASES_ROOT)
    parser.add_argument("--current", type=Path, default=DEFAULT_CURRENT)
    parser.add_argument("--whitelist", type=Path, default=DEFAULT_WHITELIST)
    parser.add_argument("--base-url", default="https://x-gu.ru")
    parser.add_argument(
        "--unsafe-skip-predeploy",
        action="store_true",
        help="emergency-only override: skip strict SEO/policy predeploy gate",
    )
    parser.add_argument("--apply", action="store_true", help="atomically switch current to release_dir")
    args = parser.parse_args()

    release = args.release_dir.resolve()
    releases_root = args.releases_root.resolve()
    current = args.current

    errors = validate_release(release, releases_root=releases_root)
    if errors:
        print("Release validation: FAIL", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 2

    if not args.unsafe_skip_predeploy:
        ok, gate_errors, _audit = run_predeploy(
            release,
            keep_config=release / KEEP_FILENAME,
            whitelist=args.whitelist.resolve(),
            base_url=args.base_url,
        )
        if not ok:
            print("Strict pre-deploy gate: FAIL", file=sys.stderr)
            for error in gate_errors:
                print(f"  - {error}", file=sys.stderr)
            return 3
        print("Strict pre-deploy gate: OK")
    else:
        print("WARNING: strict pre-deploy gate skipped by emergency override", file=sys.stderr)

    try:
        previous = _current_target(current)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 4

    if previous is not None and previous.resolve() == release:
        print(f"current already points to release: {release}")
        return 0

    print("Release validation: OK")
    print(f"release:       {release}")
    print(f"releases root: {releases_root}")
    print(f"current:       {current}")
    print(f"previous:      {previous or '(none)'}")

    if not args.apply:
        print("[DRY-RUN] Symlink not changed. Re-run with --apply only after all gates pass.")
        return 0

    try:
        previous = switch_release(current, release, releases_root=releases_root)
    except RuntimeError as exc:
        print(f"Release switch refused: {exc}", file=sys.stderr)
        return 5

    print(f"[APPLIED] {current} -> {release}")
    if previous is not None:
        print(f"rollback target: {previous}")
        print(
            f"rollback command: {sys.executable} {Path(__file__).name} {previous} "
            f"--releases-root {releases_root} --current {current} --whitelist {args.whitelist} --apply"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
