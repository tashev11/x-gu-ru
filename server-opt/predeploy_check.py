#!/usr/bin/env python3
"""Strict offline gate for a finalized x-gu.ru release before symlink switch."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from release_integrity import RELEASE_METADATA_FILENAME, verify_release_metadata  # noqa: E402
from seo_healthcheck import evaluate, run_audit  # noqa: E402


KEEP_FILENAME = ".xgu-index-keep.json"
WHITELIST_FILENAME = ".xgu-whitelist.txt"
REQUIRED_POLICY_METADATA = (
    "policy_source",
    "policy_sha256",
    "whitelist_source",
    "whitelist_sha256",
)


def _valid_sha256(value: object) -> bool:
    digest = str(value or "").strip().lower()
    return len(digest) == 64 and all(char in "0123456789abcdef" for char in digest)


def _validate_whitelist(whitelist: Path, base_url: str) -> list[str]:
    if not whitelist.is_file():
        return [f"whitelist missing: {whitelist}"]

    errors: list[str] = []
    base = urlparse(base_url.rstrip("/"))
    try:
        lines = whitelist.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError) as exc:
        return [f"cannot read whitelist: {exc}"]

    seen: set[str] = set()
    for line_number, line in enumerate(lines, start=1):
        value = line.strip()
        if not value:
            continue
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.netloc != base.netloc:
            errors.append(f"whitelist line {line_number} is not canonical HTTPS: {value}")
            continue
        if parsed.query or parsed.fragment:
            errors.append(f"whitelist line {line_number} contains query/fragment: {value}")
            continue
        decoded_parts = [unquote(part) for part in parsed.path.split("/") if part]
        if any(part in {".", ".."} or "/" in part or "\\" in part for part in decoded_parts):
            errors.append(f"whitelist line {line_number} contains unsafe path segment: {value}")
            continue
        if len(decoded_parts) > 2:
            errors.append(f"whitelist line {line_number} has unsupported path depth: {value}")
            continue
        if value in seen:
            errors.append(f"whitelist line {line_number} duplicates an earlier URL: {value}")
            continue
        seen.add(value)
    return errors


def validate_policy_files(
    keep_config: Path,
    whitelist: Path,
    *,
    release_root: Path | None = None,
    base_url: str = "https://x-gu.ru",
) -> list[str]:
    errors: list[str] = []
    if release_root is not None:
        root = release_root.resolve()
        expected_keep = root / KEEP_FILENAME
        expected_whitelist = root / WHITELIST_FILENAME
        if keep_config.resolve() != expected_keep:
            errors.append(
                f"keep-config must be the release manifest: expected={expected_keep} got={keep_config.resolve()}"
            )
        if whitelist.resolve() != expected_whitelist:
            errors.append(
                f"whitelist must be the release snapshot: expected={expected_whitelist} got={whitelist.resolve()}"
            )

    if not keep_config.is_file():
        errors.append(f"index keep-config missing: {keep_config}")
    errors.extend(_validate_whitelist(whitelist, base_url))
    if not keep_config.is_file():
        return errors

    try:
        payload = json.loads(keep_config.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"index keep-config is invalid JSON: {exc}")
        return errors

    if not isinstance(payload, dict):
        errors.append("index keep-config root must be a JSON object")
        return errors
    if not payload.get("open_cities"):
        errors.append("index keep-config has no open_cities")
    if not payload.get("open_services"):
        errors.append("index keep-config has no open_services")
    for key in REQUIRED_POLICY_METADATA:
        if not str(payload.get(key) or "").strip():
            errors.append(f"index keep-config has no {key}")

    if payload.get("policy_sha256") and not _valid_sha256(payload.get("policy_sha256")):
        errors.append("index keep-config policy_sha256 is not a valid SHA-256 hex digest")
    if payload.get("whitelist_sha256") and not _valid_sha256(payload.get("whitelist_sha256")):
        errors.append("index keep-config whitelist_sha256 is not a valid SHA-256 hex digest")

    if whitelist.is_file() and _valid_sha256(payload.get("whitelist_sha256")):
        actual = hashlib.sha256(whitelist.read_bytes()).hexdigest()
        expected = str(payload.get("whitelist_sha256")).strip().lower()
        if actual != expected:
            errors.append(f"release whitelist SHA-256 mismatch: manifest={expected} actual={actual}")
    return errors


def run_predeploy(
    release_root: Path,
    *,
    keep_config: Path,
    whitelist: Path,
    base_url: str,
) -> tuple[bool, list[str], dict | None]:
    release_root = release_root.resolve()
    errors = validate_policy_files(
        keep_config,
        whitelist,
        release_root=release_root,
        base_url=base_url,
    )
    if not release_root.is_dir():
        errors.append(f"release root not found: {release_root}")
    if errors:
        return False, errors, None

    # Finalization is the boundary after which a release is immutable. Verify
    # the full-file fingerprint before doing the semantic SEO audit so a late
    # mutation can never be deployed merely because its HTML still looks valid.
    metadata, integrity_errors = verify_release_metadata(release_root)
    if integrity_errors:
        return False, integrity_errors, None
    assert metadata is not None

    try:
        audit = run_audit(
            release_root,
            base_url=base_url,
            keep_config=keep_config,
            whitelist=whitelist,
        )
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return False, [f"SEO audit could not be completed: {exc}"], None

    if not audit.get("policy_loaded"):
        return False, ["SEO audit did not load release index policy"], audit
    if audit["stats"].get("policy_checked_pages") != audit["stats"].get("pages_total"):
        errors.append("SEO audit did not policy-check every HTML page")

    ok, breaches = evaluate(audit)
    if not ok:
        errors.extend(breaches)

    audit["release_metadata"] = metadata
    return not errors, errors, audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("release_root", type=Path, help="finalized release directory to validate")
    parser.add_argument("--base-url", default="https://x-gu.ru")
    parser.add_argument("--keep-config", type=Path, default=None, help="normally omitted; release manifest is used")
    parser.add_argument("--whitelist", type=Path, default=None, help="normally omitted; release whitelist snapshot is used")
    args = parser.parse_args()

    release_root = args.release_root.resolve()
    keep_config = args.keep_config.resolve() if args.keep_config else release_root / KEEP_FILENAME
    whitelist = args.whitelist.resolve() if args.whitelist else release_root / WHITELIST_FILENAME
    ok, errors, audit = run_predeploy(
        release_root,
        keep_config=keep_config,
        whitelist=whitelist,
        base_url=args.base_url,
    )

    if not ok:
        print("Pre-deploy check: FAIL", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 2

    assert audit is not None
    stats = audit["stats"]
    metadata = audit["release_metadata"]
    print("Pre-deploy check: OK")
    print(f"  pages={stats['pages_total']}")
    print(f"  sitemap_urls={audit['sitemap_urls']}")
    print(f"  policy_checked_pages={stats['policy_checked_pages']}")
    print(f"  noindex_total={stats['has_noindex']}")
    print(f"  tooling_revision={metadata['tooling_revision']}")
    print(f"  release_content_sha256={metadata['content_sha256']}")
    print("  release fingerprint + policy + whitelist provenance/hash verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
