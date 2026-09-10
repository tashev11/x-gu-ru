#!/usr/bin/env python3
"""Strict offline gate for a built x-gu.ru release before symlink switch."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from seo_healthcheck import evaluate, run_audit  # noqa: E402


KEEP_FILENAME = ".xgu-index-keep.json"
REQUIRED_POLICY_METADATA = ("policy_source", "policy_sha256")


def _validate_whitelist(whitelist: Path, base_url: str) -> list[str]:
    if not whitelist.is_file():
        return [f"whitelist missing: {whitelist}"]
    errors: list[str] = []
    base = urlparse(base_url.rstrip("/"))
    try:
        lines = whitelist.read_text(encoding="utf-8", errors="strict").splitlines()
    except (OSError, UnicodeError) as exc:
        return [f"cannot read whitelist: {exc}"]

    for line_number, line in enumerate(lines, start=1):
        value = line.strip()
        if not value:
            continue
        if value.startswith("/"):
            value = base_url.rstrip("/") + value
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.netloc != base.netloc:
            errors.append(f"whitelist line {line_number} is not canonical HTTPS: {line.strip()}")
            continue
        if parsed.query or parsed.fragment:
            errors.append(f"whitelist line {line_number} contains query/fragment: {line.strip()}")
            continue
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) not in {1, 2}:
            errors.append(f"whitelist line {line_number} has unsupported path depth: {line.strip()}")
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
        expected = (release_root.resolve() / KEEP_FILENAME)
        if keep_config.resolve() != expected:
            errors.append(f"keep-config must be the release manifest: expected={expected} got={keep_config.resolve()}")

    if not keep_config.is_file():
        errors.append(f"index keep-config missing: {keep_config}")
        return errors + _validate_whitelist(whitelist, base_url)

    errors.extend(_validate_whitelist(whitelist, base_url))
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

    digest = str(payload.get("policy_sha256") or "").strip().lower()
    if digest and (len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest)):
        errors.append("index keep-config policy_sha256 is not a valid SHA-256 hex digest")
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
    return not errors, errors, audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("release_root", type=Path, help="built release directory to validate")
    parser.add_argument("--base-url", default="https://x-gu.ru")
    parser.add_argument("--keep-config", type=Path, default=None, help="normally omitted; release manifest is used")
    parser.add_argument("--whitelist", type=Path, default=Path("/opt/p3-app/data/whitelist.txt"))
    args = parser.parse_args()

    release_root = args.release_root.resolve()
    keep_config = args.keep_config.resolve() if args.keep_config else release_root / KEEP_FILENAME
    ok, errors, audit = run_predeploy(
        release_root,
        keep_config=keep_config,
        whitelist=args.whitelist.resolve(),
        base_url=args.base_url,
    )

    if not ok:
        print("Pre-deploy check: FAIL", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 2

    assert audit is not None
    stats = audit["stats"]
    print("Pre-deploy check: OK")
    print(f"  pages={stats['pages_total']}")
    print(f"  sitemap_urls={audit['sitemap_urls']}")
    print(f"  policy_checked_pages={stats['policy_checked_pages']}")
    print(f"  noindex_total={stats['has_noindex']}")
    print("  release policy provenance present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
