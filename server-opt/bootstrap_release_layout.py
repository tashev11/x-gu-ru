#!/usr/bin/env python3
"""Bootstrap an existing real ``current`` directory into the release layout.

This is a one-time migration helper for servers where
``/var/www/x-gu.ru/current`` is still a real directory. It does not copy the
site: on apply it renames that exact directory to a direct child of
``releases_root`` and immediately installs ``current`` as a symlink to it.

Dry-run by default. The operation is intentionally conservative and refuses
cross-filesystem moves, existing release destinations, symlinked current paths,
or suspicious layouts. If symlink installation fails after the rename, the
helper attempts to rename the release back to ``current``.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


DEFAULT_CURRENT = Path("/var/www/x-gu.ru/current")
DEFAULT_RELEASES_ROOT = Path("/var/www/x-gu.ru/releases")


def _same_filesystem(left: Path, right_parent: Path) -> bool:
    return left.stat().st_dev == right_parent.stat().st_dev


def _validate_layout(current: Path, releases_root: Path, release: Path) -> list[str]:
    errors: list[str] = []

    if current.is_symlink():
        errors.append(f"current is already a symlink: {current}")
    elif not current.is_dir():
        errors.append(f"current is not a real directory: {current}")

    if releases_root.exists() and not releases_root.is_dir():
        errors.append(f"releases root exists but is not a directory: {releases_root}")

    expected_parent = releases_root.resolve() if releases_root.exists() else releases_root.parent.resolve() / releases_root.name
    release_parent = release.parent.resolve() if release.parent.exists() else release.parent.parent.resolve() / release.parent.name
    if release_parent != expected_parent:
        errors.append(f"release destination must be a direct child of releases root: {release}")

    if release.exists() or release.is_symlink():
        errors.append(f"release destination already exists: {release}")

    try:
        if current.resolve() == releases_root.resolve():
            errors.append("current and releases root cannot be the same path")
    except OSError:
        pass

    # Require the key static-site files before moving the live directory.
    if current.is_dir() and not current.is_symlink():
        for name in ("index.html", "robots.txt", "sitemap.xml"):
            path = current / name
            if not path.is_file() or path.stat().st_size == 0:
                errors.append(f"current is missing required non-empty file: {path}")

    return errors


def bootstrap(current: Path, releases_root: Path, release: Path) -> None:
    """Rename real current into releases and replace it with a symlink.

    The caller must have run ``_validate_layout`` first. Any failure after the
    rename triggers a best-effort rollback to the original directory path.
    """
    releases_root.mkdir(parents=True, exist_ok=True)
    if not _same_filesystem(current, releases_root):
        raise RuntimeError("current and releases root are on different filesystems; atomic rename is impossible")

    temp_link = current.parent / f".{current.name}.bootstrap.{os.getpid()}.{time.time_ns()}"
    os.symlink(str(release.resolve()), temp_link)

    moved = False
    try:
        os.rename(current, release)
        moved = True
        os.replace(temp_link, current)
    except Exception:
        if temp_link.is_symlink():
            temp_link.unlink()
        if moved and not current.exists() and not current.is_symlink() and release.is_dir():
            try:
                os.rename(release, current)
            except Exception as rollback_exc:  # noqa: BLE001
                raise RuntimeError(
                    f"bootstrap failed and automatic rollback also failed; live directory is at {release}: {rollback_exc}"
                ) from rollback_exc
        raise
    finally:
        if temp_link.is_symlink():
            temp_link.unlink()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--current", type=Path, default=DEFAULT_CURRENT)
    parser.add_argument("--releases-root", type=Path, default=DEFAULT_RELEASES_ROOT)
    parser.add_argument(
        "--release-name",
        default="",
        help="bootstrap release directory name; default: bootstrap-YYYYmmdd-HHMMSS",
    )
    parser.add_argument("--apply", action="store_true", help="perform the one-time directory->symlink migration")
    args = parser.parse_args()

    current = args.current.absolute()
    releases_root = args.releases_root.absolute()
    release_name = args.release_name.strip() or time.strftime("bootstrap-%Y%m%d-%H%M%S")
    if "/" in release_name or release_name in {".", ".."}:
        print("Invalid --release-name; use one directory name only.", file=sys.stderr)
        return 2
    release = releases_root / release_name

    errors = _validate_layout(current, releases_root, release)
    if errors:
        print("Bootstrap validation: FAIL", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 2

    parent = releases_root if releases_root.exists() else releases_root.parent
    if not parent.is_dir():
        print(f"Parent directory does not exist: {parent}", file=sys.stderr)
        return 2
    if not _same_filesystem(current, parent):
        print("Refusing bootstrap: current and releases root parent are on different filesystems.", file=sys.stderr)
        return 3

    print("Bootstrap validation: OK")
    print(f"current directory: {current}")
    print(f"release target:    {release}")
    print(f"new symlink:       {current} -> {release}")
    print("No site copy will be made; the existing directory is renamed on the same filesystem.")

    if not args.apply:
        print("[DRY-RUN] Nothing changed. Re-run with --apply only during a controlled maintenance window.")
        return 0

    try:
        bootstrap(current, releases_root, release)
    except Exception as exc:  # noqa: BLE001
        print(f"Bootstrap failed: {exc}", file=sys.stderr)
        return 4

    if not current.is_symlink() or current.resolve() != release.resolve():
        print("Bootstrap verification failed after apply; inspect filesystem immediately.", file=sys.stderr)
        return 5

    print(f"[APPLIED] {current} is now a symlink to {release}")
    print("Immediately run local Nginx/static checks before any further deployment step.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
