#!/usr/bin/env python3
"""Validate and atomically switch x-gu.ru to a finalized release."""
from __future__ import annotations

import argparse
import hashlib
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

from index_policy import normalize_policy_payload, policy_digest  # noqa: E402
from predeploy_check import run_predeploy  # noqa: E402
from release_integrity import RELEASE_METADATA_FILENAME, verify_release_metadata  # noqa: E402
from release_safety import DEFAULT_RELEASE_LOCK, release_operation_lock  # noqa: E402


DEFAULT_RELEASES_ROOT = Path("/var/www/x-gu.ru/releases")
DEFAULT_CURRENT = Path("/var/www/x-gu.ru/current")
KEEP_FILENAME = ".xgu-index-keep.json"
WHITELIST_FILENAME = ".xgu-whitelist.txt"
CANONICAL_HOST = "x-gu.ru"


def _required_paths(release: Path) -> list[Path]:
    return [
        release / "index.html",
        release / "robots.txt",
        release / "sitemap.xml",
        release / KEEP_FILENAME,
        release / WHITELIST_FILENAME,
        release / RELEASE_METADATA_FILENAME,
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
    try:
        root = ET.fromstring(sitemap.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, ET.ParseError) as exc:
        return [f"sitemap.xml is not valid XML: {exc}"]

    root_name = _local_name(root.tag)
    if root_name == "urlset":
        return []
    if root_name != "sitemapindex":
        return ["sitemap.xml root must be sitemapindex or urlset"]

    locs = [
        node.text.strip()
        for node in root.iter()
        if _local_name(node.tag) == "loc" and node.text and node.text.strip()
    ]
    if not locs:
        return ["sitemap index contains no shard locations"]

    errors: list[str] = []
    for loc in locs:
        parsed = urlparse(loc)
        if parsed.scheme != "https" or parsed.hostname != CANONICAL_HOST or parsed.query or parsed.fragment:
            errors.append(f"sitemap shard loc is not canonical HTTPS: {loc}")
            continue
        rel = parsed.path.lstrip("/")
        shard = release / rel
        try:
            shard.resolve().relative_to(release.resolve())
        except ValueError:
            errors.append(f"sitemap shard escapes release root: {loc}")
            continue
        if not shard.is_file():
            errors.append(f"referenced sitemap shard missing: {rel}")
        elif shard.stat().st_size == 0:
            errors.append(f"referenced sitemap shard is empty: {rel}")
    return errors


def _valid_sha256(value: object) -> bool:
    digest = str(value or "").strip().lower()
    return len(digest) == 64 and all(char in "0123456789abcdef" for char in digest)


def _validate_keep_manifest(path: Path, whitelist: Path) -> list[str]:
    if not path.is_file():
        return [f"required release file missing: {KEEP_FILENAME}"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return [f"{KEEP_FILENAME} is invalid JSON: {exc}"]
    if not isinstance(payload, dict):
        return [f"{KEEP_FILENAME} root must be a JSON object"]

    errors: list[str] = []
    try:
        policy = normalize_policy_payload(payload)
    except ValueError as exc:
        errors.append(f"{KEEP_FILENAME} policy is invalid: {exc}")
        policy = None

    for key in ("policy_source", "whitelist_source"):
        if not str(payload.get(key) or "").strip():
            errors.append(f"{KEEP_FILENAME} has no {key}")
    for key in ("policy_sha256", "whitelist_sha256"):
        if not _valid_sha256(payload.get(key)):
            errors.append(f"{KEEP_FILENAME} has invalid {key}")

    if policy is not None and _valid_sha256(payload.get("policy_sha256")):
        expected = str(payload.get("policy_sha256")).strip().lower()
        actual = policy_digest(policy)
        if actual != expected:
            errors.append(f"{KEEP_FILENAME} policy SHA-256 mismatch: manifest={expected} actual={actual}")

    if whitelist.is_file() and _valid_sha256(payload.get("whitelist_sha256")):
        expected = str(payload.get("whitelist_sha256")).strip().lower()
        actual = hashlib.sha256(whitelist.read_bytes()).hexdigest()
        if actual != expected:
            errors.append(f"{KEEP_FILENAME} whitelist SHA-256 mismatch: manifest={expected} actual={actual}")
    return errors


def validate_release(release: Path, *, releases_root: Path | None = None) -> list[str]:
    if not release.is_dir():
        return [f"release directory not found: {release}"]

    errors: list[str] = []
    if releases_root is not None:
        errors.extend(_validate_release_location(release, releases_root))

    for path in _required_paths(release):
        if not path.is_file():
            errors.append(f"required release file missing: {path.name}")
        elif path.stat().st_size == 0:
            errors.append(f"required release file is empty: {path.name}")

    index = release / "index.html"
    if index.is_file():
        text = index.read_text(encoding="utf-8", errors="ignore").lower()
        if "<html" not in text:
            errors.append("index.html does not look like HTML")
        if "<title" not in text:
            errors.append("index.html has no title")

    sitemap = release / "sitemap.xml"
    if sitemap.is_file() and sitemap.stat().st_size:
        errors.extend(_validate_sitemap_index(release, sitemap))

    keep = release / KEEP_FILENAME
    whitelist = release / WHITELIST_FILENAME
    if keep.is_file():
        errors.extend(_validate_keep_manifest(keep, whitelist))

    # Finalized release integrity is mandatory even when the emergency caller
    # explicitly skips the expensive SEO predeploy audit. The override must not
    # turn into permission to deploy a release mutated after finalization.
    if (release / RELEASE_METADATA_FILENAME).is_file():
        _metadata, integrity_errors = verify_release_metadata(release)
        errors.extend(integrity_errors)

    return list(dict.fromkeys(errors))


def _current_target(current: Path) -> Path | None:
    if not current.exists() and not current.is_symlink():
        return None
    if not current.is_symlink():
        raise RuntimeError(f"{current} is not a symlink; refusing to replace a real directory/file atomically")
    raw = Path(os.readlink(current))
    return raw.resolve() if raw.is_absolute() else (current.parent / raw).resolve()


def switch_release(current: Path, release: Path, *, releases_root: Path) -> Path | None:
    location_errors = _validate_release_location(release, releases_root)
    if location_errors:
        raise RuntimeError(location_errors[0])

    previous = _current_target(current)
    temp_link = current.parent / f".{current.name}.next.{os.getpid()}.{time.time_ns()}"
    try:
        os.symlink(str(release.resolve()), temp_link)
        os.replace(temp_link, current)
    finally:
        if temp_link.is_symlink():
            temp_link.unlink()
    return previous


def _validate_candidate(
    release: Path,
    releases_root: Path,
    base_url: str,
    unsafe_skip: bool,
) -> tuple[int, list[str], dict | None]:
    errors = validate_release(release, releases_root=releases_root)
    if errors:
        return 2, errors, None
    if unsafe_skip:
        # Structural policy, embedded whitelist hash and finalized release
        # fingerprint already passed in validate_release(). Only the heavier SEO
        # healthcheck portion is skipped here.
        return 0, [], None

    ok, gate_errors, audit = run_predeploy(
        release,
        keep_config=release / KEEP_FILENAME,
        whitelist=release / WHITELIST_FILENAME,
        base_url=base_url,
    )
    return (0, [], audit) if ok else (3, gate_errors, audit)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("release_dir", type=Path, help="fully finalized release directory")
    parser.add_argument("--releases-root", type=Path, default=DEFAULT_RELEASES_ROOT)
    parser.add_argument("--current", type=Path, default=DEFAULT_CURRENT)
    parser.add_argument("--lock-file", type=Path, default=DEFAULT_RELEASE_LOCK)
    parser.add_argument("--base-url", default="https://x-gu.ru")
    parser.add_argument(
        "--unsafe-skip-predeploy",
        action="store_true",
        help=(
            "emergency-only: skip the heavy SEO predeploy audit; release fingerprint, "
            "policy digest and embedded whitelist hash remain mandatory"
        ),
    )
    parser.add_argument("--apply", action="store_true", help="atomically switch current to release_dir")
    args = parser.parse_args()

    release = args.release_dir.resolve()
    releases_root = args.releases_root.resolve()
    current = args.current

    def validate_and_report() -> tuple[int, Path | None, dict | None]:
        code, errors, audit = _validate_candidate(release, releases_root, args.base_url, args.unsafe_skip_predeploy)
        if errors:
            label = "Release validation" if code == 2 else "Strict pre-deploy gate"
            print(f"{label}: FAIL", file=sys.stderr)
            for error in errors:
                print(f"  - {error}", file=sys.stderr)
            return code, None, audit
        if args.unsafe_skip_predeploy:
            print(
                "WARNING: heavy SEO predeploy audit skipped by emergency override; "
                "release fingerprint + policy + whitelist integrity still passed",
                file=sys.stderr,
            )
        else:
            print("Strict pre-deploy gate: OK")
        previous = _current_target(current)
        if audit and audit.get("release_metadata"):
            metadata = audit["release_metadata"]
            print(f"policy:           v{audit.get('policy_version')} {audit.get('policy_mode')}")
            print(f"tooling revision: {metadata['tooling_revision']}")
            print(f"release sha256:   {metadata['content_sha256']}")
        return 0, previous, audit

    if not args.apply:
        try:
            code, previous, _audit = validate_and_report()
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            return 4
        if code:
            return code
        if previous is not None and previous.resolve() == release:
            print(f"current already points to release: {release}")
            return 0
        print(f"release:       {release}")
        print(f"releases root: {releases_root}")
        print(f"current:       {current}")
        print(f"previous:      {previous or '(none)'}")
        print("[DRY-RUN] Symlink not changed. Re-run with --apply only after all gates pass.")
        return 0

    try:
        with release_operation_lock(args.lock_file.resolve()):
            code, previous, _audit = validate_and_report()
            if code:
                return code
            if previous is not None and previous.resolve() == release:
                print(f"current already points to release: {release}")
                return 0
            previous = switch_release(current, release, releases_root=releases_root)
    except (RuntimeError, FileNotFoundError) as exc:
        print(f"Release switch refused: {exc}", file=sys.stderr)
        return 5

    print(f"[APPLIED] {current} -> {release}")
    if previous is not None:
        print(f"rollback target: {previous}")
        print(
            f"rollback command: {sys.executable} {Path(__file__).name} {previous} "
            f"--releases-root {releases_root} --current {current} --lock-file {args.lock_file} --apply"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
