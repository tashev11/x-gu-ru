#!/usr/bin/env python3
"""Bootstrap a legacy real ``current`` directory into the release layout.

This one-time helper is for servers where ``/var/www/x-gu.ru/current`` is still
a real directory. It never makes that legacy directory the new release.
Instead, an already-built and predeploy-clean self-contained release candidate
must be supplied as the target. During the short cutover the legacy directory
is renamed into ``releases_root`` as an emergency backup and ``current`` is
replaced with a symlink to the validated candidate.

Dry-run by default. Apply is maintenance-window only. If symlink installation
fails after the legacy directory rename, the helper attempts to restore that
original directory back to ``current`` automatically.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


SERVER_OPT = Path(__file__).resolve().parent
if str(SERVER_OPT) not in sys.path:
    sys.path.insert(0, str(SERVER_OPT))

from predeploy_check import KEEP_FILENAME, WHITELIST_FILENAME, run_predeploy  # noqa: E402


DEFAULT_CURRENT = Path("/var/www/x-gu.ru/current")
DEFAULT_RELEASES_ROOT = Path("/var/www/x-gu.ru/releases")
REQUIRED_STATIC_FILES = ("index.html", "robots.txt", "sitemap.xml")


def _same_filesystem(left: Path, right: Path) -> bool:
    return left.stat().st_dev == right.stat().st_dev


def _direct_child(path: Path, root: Path) -> bool:
    try:
        return path.resolve().parent == root.resolve()
    except OSError:
        return False


def _validate_layout(
    current: Path,
    releases_root: Path,
    target_release: Path,
    backup_release: Path,
) -> list[str]:
    errors: list[str] = []

    if current.is_symlink():
        errors.append(f"current is already a symlink: {current}")
    elif not current.is_dir():
        errors.append(f"current is not a real directory: {current}")

    if not releases_root.is_dir():
        errors.append(f"releases root is not a directory: {releases_root}")

    if not target_release.is_dir() or target_release.is_symlink():
        errors.append(f"target release is not a real directory: {target_release}")
    elif not _direct_child(target_release, releases_root):
        errors.append(f"target release must be a direct child of releases root: {target_release}")

    if backup_release.exists() or backup_release.is_symlink():
        errors.append(f"backup release already exists: {backup_release}")
    if backup_release.parent.resolve() != releases_root.resolve():
        errors.append(f"backup release must be a direct child of releases root: {backup_release}")

    if target_release.resolve() == backup_release.resolve():
        errors.append("target release and legacy backup release must differ")

    if current.is_dir() and not current.is_symlink():
        for name in REQUIRED_STATIC_FILES:
            path = current / name
            if not path.is_file() or path.stat().st_size == 0:
                errors.append(f"legacy current is missing required non-empty file: {path}")

    if target_release.is_dir() and not target_release.is_symlink():
        for name in (*REQUIRED_STATIC_FILES, KEEP_FILENAME, WHITELIST_FILENAME):
            path = target_release / name
            if not path.is_file() or path.stat().st_size == 0:
                errors.append(f"target release is missing required non-empty file: {path}")

    if current.exists() and releases_root.exists():
        try:
            if not _same_filesystem(current, releases_root):
                errors.append("current and releases root are on different filesystems; atomic rename is impossible")
        except OSError as exc:
            errors.append(f"cannot verify filesystem layout: {exc}")

    return errors


def bootstrap(
    current: Path,
    releases_root: Path,
    target_release: Path,
    backup_release: Path,
) -> None:
    """Archive real current and point current at the validated target release."""
    if not _same_filesystem(current, releases_root):
        raise RuntimeError("current and releases root are on different filesystems; atomic rename is impossible")

    temp_link = current.parent / f".{current.name}.bootstrap.{os.getpid()}.{time.time_ns()}"
    os.symlink(str(target_release.resolve()), temp_link)

    moved = False
    try:
        os.rename(current, backup_release)
        moved = True
        os.replace(temp_link, current)
    except Exception:
        if temp_link.is_symlink():
            temp_link.unlink()
        if moved and not current.exists() and not current.is_symlink() and backup_release.is_dir():
            try:
                os.rename(backup_release, current)
            except Exception as rollback_exc:  # noqa: BLE001
                raise RuntimeError(
                    "bootstrap failed and automatic rollback also failed; "
                    f"legacy live directory remains at {backup_release}: {rollback_exc}"
                ) from rollback_exc
        raise
    finally:
        if temp_link.is_symlink():
            temp_link.unlink()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("target_release", type=Path, help="already-built self-contained release candidate")
    parser.add_argument("--current", type=Path, default=DEFAULT_CURRENT)
    parser.add_argument("--releases-root", type=Path, default=DEFAULT_RELEASES_ROOT)
    parser.add_argument(
        "--backup-name",
        default="",
        help="legacy-current backup name; default: pre-bootstrap-YYYYmmdd-HHMMSS",
    )
    parser.add_argument("--base-url", default="https://x-gu.ru")
    parser.add_argument("--apply", action="store_true", help="perform the one-time directory->symlink cutover")
    args = parser.parse_args()

    current = args.current.absolute()
    releases_root = args.releases_root.absolute()
    target_release = args.target_release.absolute()
    backup_name = args.backup_name.strip() or time.strftime("pre-bootstrap-%Y%m%d-%H%M%S")
    if "/" in backup_name or backup_name in {".", ".."}:
        print("Invalid --backup-name; use one directory name only.", file=sys.stderr)
        return 2
    backup_release = releases_root / backup_name

    errors = _validate_layout(current, releases_root, target_release, backup_release)
    if errors:
        print("Bootstrap validation: FAIL", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 2

    ok, gate_errors, _audit = run_predeploy(
        target_release,
        keep_config=target_release / KEEP_FILENAME,
        whitelist=target_release / WHITELIST_FILENAME,
        base_url=args.base_url,
    )
    if not ok:
        print("Bootstrap target predeploy: FAIL", file=sys.stderr)
        for error in gate_errors:
            print(f"  - {error}", file=sys.stderr)
        return 3

    print("Bootstrap target predeploy: OK")
    print(f"legacy current: {current}")
    print(f"target release: {target_release}")
    print(f"legacy backup:  {backup_release}")
    print(f"new symlink:    {current} -> {target_release}")
    print("The validated target is not modified by bootstrap.")

    if not args.apply:
        print("[DRY-RUN] Nothing changed. Re-run with --apply only during a controlled maintenance window.")
        return 0

    try:
        bootstrap(current, releases_root, target_release, backup_release)
    except Exception as exc:  # noqa: BLE001
        print(f"Bootstrap failed: {exc}", file=sys.stderr)
        return 4

    if not current.is_symlink() or current.resolve() != target_release.resolve():
        print("Bootstrap verification failed after apply; inspect filesystem immediately.", file=sys.stderr)
        return 5
    if not backup_release.is_dir():
        print("Bootstrap verification failed: legacy backup directory is missing.", file=sys.stderr)
        return 5

    print(f"[APPLIED] {current} -> {target_release}")
    print(f"emergency legacy backup: {backup_release}")
    print("Immediately run local Nginx/static and SEO health checks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
