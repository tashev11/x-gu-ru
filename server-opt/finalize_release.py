#!/usr/bin/env python3
"""Finalize a release candidate with code provenance and a content fingerprint.

Run this after all candidate mutations and before strict predeploy/deploy. Dry-run
computes the fingerprint but writes nothing. ``--apply`` writes only
``.xgu-release.json`` and is allowed only for an inactive direct child of the
releases root.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from release_integrity import build_release_metadata, write_release_metadata  # noqa: E402
from release_safety import DEFAULT_CURRENT, DEFAULT_RELEASES_ROOT, mutation_target_error  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("release_dir", type=Path)
    parser.add_argument(
        "--tooling-revision",
        default=os.getenv("XGU_TOOLING_REVISION", ""),
        help="full 40-character Git SHA of the validated tooling checkout",
    )
    parser.add_argument("--source-release", default="", help="optional previous release path/name used as build source")
    parser.add_argument("--current", type=Path, default=DEFAULT_CURRENT)
    parser.add_argument("--releases-root", type=Path, default=DEFAULT_RELEASES_ROOT)
    parser.add_argument("--apply", action="store_true", help="write .xgu-release.json")
    args = parser.parse_args()

    release = args.release_dir.resolve()
    if not release.is_dir():
        print(f"Release directory not found: {release}", file=sys.stderr)
        return 2

    target_error = mutation_target_error(
        release,
        current=args.current,
        releases_root=args.releases_root,
        allow_active_current=False,
    )
    if target_error:
        print(f"Refusing finalization: {target_error}", file=sys.stderr)
        return 3

    revision = args.tooling_revision.strip()
    if not revision:
        print("Full tooling Git SHA is required via --tooling-revision or XGU_TOOLING_REVISION.", file=sys.stderr)
        return 4

    finalized_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        payload = build_release_metadata(
            release,
            tooling_revision=revision,
            finalized_at=finalized_at,
            source_release=args.source_release,
        )
    except (OSError, ValueError) as exc:
        print(f"Release finalization failed: {exc}", file=sys.stderr)
        return 5

    print(f"release:          {release}")
    print(f"tooling revision: {payload['tooling_revision']}")
    print(f"files:            {payload['file_count']}")
    print(f"bytes:            {payload['total_bytes']}")
    print(f"content sha256:   {payload['content_sha256']}")

    if not args.apply:
        print("[DRY-RUN] Integrity metadata not written. Re-run with --apply after reviewing the candidate.")
        return 0

    try:
        path = write_release_metadata(release, payload)
    except OSError as exc:
        print(f"Failed to write release metadata: {exc}", file=sys.stderr)
        return 6

    print(f"[APPLIED] release finalized: {path}")
    print("Do not modify candidate files after finalization; predeploy/deploy will detect any change.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
