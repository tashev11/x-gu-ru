#!/usr/bin/env python3
"""Strict offline gate for a built x-gu.ru release before symlink switch.

Unlike the general SEO healthcheck, this command requires the production index
policy and whitelist to exist and requires policy provenance metadata. It does
not mutate files.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from seo_healthcheck import evaluate, run_audit  # noqa: E402


REQUIRED_POLICY_METADATA = ("policy_source", "policy_sha256")


def validate_policy_files(keep_config: Path, whitelist: Path) -> list[str]:
    errors: list[str] = []
    if not keep_config.is_file():
        errors.append(f"index keep-config missing: {keep_config}")
        return errors
    if not whitelist.is_file():
        errors.append(f"whitelist missing: {whitelist}")

    try:
        payload = json.loads(keep_config.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"index keep-config is invalid JSON: {exc}")
        return errors

    if not payload.get("open_cities"):
        errors.append("index keep-config has no open_cities")
    if not payload.get("open_services"):
        errors.append("index keep-config has no open_services")
    for key in REQUIRED_POLICY_METADATA:
        if not str(payload.get(key) or "").strip():
            errors.append(f"index keep-config has no {key}")

    digest = str(payload.get("policy_sha256") or "").strip().lower()
    if digest and (len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest)):
        errors.append("index keep-config policy_sha256 is not a valid SHA-256 hex digest")
    return errors


def run_predeploy(
    release_root: Path,
    *,
    keep_config: Path,
    whitelist: Path,
    base_url: str,
) -> tuple[bool, list[str], dict | None]:
    errors = validate_policy_files(keep_config, whitelist)
    if not release_root.is_dir():
        errors.append(f"release root not found: {release_root}")
    if errors:
        return False, errors, None

    audit = run_audit(
        release_root,
        base_url=base_url,
        keep_config=keep_config,
        whitelist=whitelist,
    )
    if not audit.get("policy_loaded"):
        errors.append("SEO audit did not load index policy")
        return False, errors, audit

    ok, breaches = evaluate(audit)
    if not ok:
        errors.extend(breaches)
    return not errors, errors, audit


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("release_root", type=Path, help="built release directory to validate")
    parser.add_argument("--base-url", default="https://x-gu.ru")
    parser.add_argument(
        "--keep-config",
        type=Path,
        default=Path("/opt/p3-app/data/index_keep_config.json"),
    )
    parser.add_argument(
        "--whitelist",
        type=Path,
        default=Path("/opt/p3-app/data/whitelist.txt"),
    )
    args = parser.parse_args()

    ok, errors, audit = run_predeploy(
        args.release_root.resolve(),
        keep_config=args.keep_config.resolve(),
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
    print("  policy provenance present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
