#!/usr/bin/env python3
"""Prune old x-gu.ru releases without touching active or bootstrap backups.

Dry-run by default. ``--apply`` is required for deletion. Only direct child
directories of ``releases_root`` are eligible, the current symlink target is
always protected, the newest N releases are retained, and one-time
``pre-bootstrap-*`` legacy backups are preserved unless an explicit opt-in
allows them to join the normal retention pool.
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from release_safety import DEFAULT_RELEASE_LOCK, release_operation_lock  # noqa: E402


BOOTSTRAP_BACKUP_PREFIX = "pre-bootstrap-"


def _direct_release_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    items: list[Path] = []
    for child in root.iterdir():
        if child.is_dir() and not child.is_symlink():
            items.append(child.resolve())
    return sorted(items, key=lambda path: path.stat().st_mtime, reverse=True)


def _current_release(current: Path, releases_root: Path) -> Path | None:
    if not current.is_symlink():
        return None
    root = releases_root.resolve()
    target = current.resolve()
    if target.parent != root:
        return None
    if not target.is_dir() or target.is_symlink():
        return None
    return target


def build_plan(
    releases_root: Path,
    current: Path,
    keep: int,
    *,
    protect_bootstrap_backups: bool = True,
) -> tuple[list[Path], list[Path]]:
    if keep < 1:
        raise ValueError("keep must be >= 1")
    root = releases_root.resolve()
    releases = _direct_release_dirs(root)
    active = _current_release(current, root)

    protected: set[Path] = set(releases[:keep])
    if active is not None:
        protected.add(active)
    if protect_bootstrap_backups:
        protected.update(path for path in releases if path.name.startswith(BOOTSTRAP_BACKUP_PREFIX))

    delete = [release for release in releases if release not in protected]
    keep_paths = [release for release in releases if release in protected]
    return keep_paths, delete


def _safe_delete(path: Path, releases_root: Path) -> None:
    root = releases_root.resolve()
    resolved = path.resolve()
    if resolved.parent != root:
        raise RuntimeError(f"refusing non-direct release path: {resolved}")
    if resolved == root:
        raise RuntimeError("refusing to delete releases root")
    if resolved.is_symlink():
        raise RuntimeError(f"refusing symlink release: {resolved}")
    shutil.rmtree(resolved)


def _delete_if_still_inactive(path: Path, releases_root: Path, current: Path) -> None:
    """Re-check current immediately before deletion even while the host lock is held."""
    active = _current_release(current, releases_root)
    if active is None:
        raise RuntimeError("active release cannot be proven immediately before delete")
    if path.resolve() == active:
        raise RuntimeError(f"refusing to delete release that is active now: {active}")
    _safe_delete(path, releases_root)


def _print_plan(
    releases_root: Path,
    current: Path,
    keep: int,
    keep_paths: list[Path],
    delete_paths: list[Path],
    *,
    protect_bootstrap_backups: bool,
) -> Path | None:
    active = _current_release(current, releases_root)
    print(f"releases_root: {releases_root.resolve()}")
    print(f"active: {active or '(current is not a direct release symlink)'}")
    print(f"keep newest: {keep}")
    print(f"protect pre-bootstrap backups: {protect_bootstrap_backups}")
    print(f"protected: {len(keep_paths)}; delete candidates: {len(delete_paths)}")
    for path in delete_paths:
        print(f"  DELETE {path}")
    return active


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--releases-root", type=Path, default=Path("/var/www/x-gu.ru/releases"))
    parser.add_argument("--current", type=Path, default=Path("/var/www/x-gu.ru/current"))
    parser.add_argument("--lock-file", type=Path, default=DEFAULT_RELEASE_LOCK)
    parser.add_argument("--keep", type=int, default=5, help="number of newest releases to retain")
    parser.add_argument(
        "--include-bootstrap-backups",
        action="store_true",
        help="allow pre-bootstrap-* legacy backups to enter normal retention/deletion",
    )
    parser.add_argument("--apply", action="store_true", help="delete planned old releases")
    args = parser.parse_args()

    if not args.releases_root.is_dir():
        print(f"Releases root not found: {args.releases_root}", file=sys.stderr)
        return 1

    protect_bootstrap = not args.include_bootstrap_backups

    if not args.apply:
        try:
            keep_paths, delete_paths = build_plan(
                args.releases_root,
                args.current,
                args.keep,
                protect_bootstrap_backups=protect_bootstrap,
            )
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        _print_plan(
            args.releases_root,
            args.current,
            args.keep,
            keep_paths,
            delete_paths,
            protect_bootstrap_backups=protect_bootstrap,
        )
        print("[DRY-RUN] Nothing deleted. Re-run with --apply after reviewing the plan.")
        return 0

    try:
        with release_operation_lock(args.lock_file.resolve()):
            # Rebuild the plan only after acquiring the same lock used by
            # deploy/bootstrap so no stale pre-lock plan can be applied.
            keep_paths, delete_paths = build_plan(
                args.releases_root,
                args.current,
                args.keep,
                protect_bootstrap_backups=protect_bootstrap,
            )
            active = _print_plan(
                args.releases_root,
                args.current,
                args.keep,
                keep_paths,
                delete_paths,
                protect_bootstrap_backups=protect_bootstrap,
            )
            if active is None:
                print(
                    f"Refusing apply: {args.current} does not point to a direct release inside releases_root.",
                    file=sys.stderr,
                )
                return 3

            deleted = 0
            for path in delete_paths:
                if protect_bootstrap and path.name.startswith(BOOTSTRAP_BACKUP_PREFIX):
                    print(f"Refusing protected bootstrap backup deletion: {path}", file=sys.stderr)
                    return 4
                try:
                    _delete_if_still_inactive(path, args.releases_root, args.current)
                except RuntimeError as exc:
                    print(f"Refusing deletion of {path}: {exc}", file=sys.stderr)
                    return 4
                deleted += 1
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        print(f"Release prune refused: {exc}", file=sys.stderr)
        return 4

    print(f"[APPLIED] deleted_releases={deleted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
