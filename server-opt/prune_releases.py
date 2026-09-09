#!/usr/bin/env python3
"""Prune old x-gu.ru release directories without touching the active release.

Dry-run by default. ``--apply`` is required for deletion. Only direct child
directories of ``releases_root`` are eligible, the current symlink target is
always protected, and the newest N releases are retained.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path


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
    target = current.resolve()
    try:
        target.relative_to(releases_root.resolve())
    except ValueError:
        return None
    return target


def build_plan(releases_root: Path, current: Path, keep: int) -> tuple[list[Path], list[Path]]:
    if keep < 1:
        raise ValueError("keep must be >= 1")
    root = releases_root.resolve()
    releases = _direct_release_dirs(root)
    active = _current_release(current, root)

    protected: set[Path] = set(releases[:keep])
    if active is not None:
        protected.add(active)

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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--releases-root", type=Path, default=Path("/var/www/x-gu.ru/releases"))
    parser.add_argument("--current", type=Path, default=Path("/var/www/x-gu.ru/current"))
    parser.add_argument("--keep", type=int, default=5, help="number of newest releases to retain")
    parser.add_argument("--apply", action="store_true", help="delete planned old releases")
    args = parser.parse_args()

    if not args.releases_root.is_dir():
        print(f"Releases root not found: {args.releases_root}", file=sys.stderr)
        return 1

    try:
        keep_paths, delete_paths = build_plan(args.releases_root, args.current, args.keep)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    active = _current_release(args.current, args.releases_root)
    print(f"releases_root: {args.releases_root.resolve()}")
    print(f"active: {active or '(current is not a release symlink)'}")
    print(f"keep newest: {args.keep}")
    print(f"protected: {len(keep_paths)}; delete candidates: {len(delete_paths)}")
    for path in delete_paths:
        print(f"  DELETE {path}")

    if not args.apply:
        print("[DRY-RUN] Nothing deleted. Re-run with --apply after reviewing the plan.")
        return 0

    if not args.current.is_symlink():
        print(
            f"Refusing apply: {args.current} is not a symlink; active release cannot be proven.",
            file=sys.stderr,
        )
        return 3

    active = _current_release(args.current, args.releases_root)
    if active is None:
        print("Refusing apply: current symlink does not point inside releases_root.", file=sys.stderr)
        return 4

    deleted = 0
    for path in delete_paths:
        if path == active:
            print(f"BUG GUARD: active release appeared in delete plan: {path}", file=sys.stderr)
            return 5
        _safe_delete(path, args.releases_root)
        deleted += 1

    print(f"[APPLIED] deleted_releases={deleted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
