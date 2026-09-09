#!/usr/bin/env python3
"""Install the hardened generator files into the private ``app.services`` package.

Dry-run by default. With ``--apply`` the three files are installed as one
reviewed set, existing targets are backed up, and each target is replaced via
``os.replace`` after a complete temporary copy has been written.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import time
from pathlib import Path


REQUIRED_FILES = (
    "content_generator.py",
    "_content_generator_legacy.py",
    "city_morphology.py",
)


def validate_sources(source_root: Path) -> list[str]:
    errors: list[str] = []
    for name in REQUIRED_FILES:
        path = source_root / name
        if not path.is_file():
            errors.append(f"missing source file: {path}")
        elif path.stat().st_size == 0:
            errors.append(f"empty source file: {path}")
    return errors


def install_file(source: Path, target: Path, backup_suffix: str) -> Path | None:
    backup: Path | None = None
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        backup = target.with_name(target.name + backup_suffix)
        shutil.copy2(target, backup)

    temp = target.with_name(f".{target.name}.next.{os.getpid()}")
    try:
        shutil.copy2(source, temp)
        os.replace(temp, target)
    finally:
        if temp.exists():
            temp.unlink()
    return backup


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, default=repo_root)
    parser.add_argument("--dest", type=Path, default=Path("/opt/p3-app/app/services"))
    parser.add_argument("--apply", action="store_true", help="install the hardened generator files")
    args = parser.parse_args()

    source_root = args.source_root.resolve()
    dest = args.dest.resolve()
    errors = validate_sources(source_root)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 2
    if not dest.is_dir():
        print(f"Destination app.services directory not found: {dest}", file=sys.stderr)
        return 3

    print(f"source: {source_root}")
    print(f"dest:   {dest}")
    for name in REQUIRED_FILES:
        print(f"  {source_root / name} -> {dest / name}")

    if not args.apply:
        print("[DRY-RUN] Nothing installed. Re-run with --apply after reviewing the file set.")
        return 0

    suffix = ".bak." + time.strftime("%Y%m%d-%H%M%S")
    backups: list[Path] = []
    installed: list[Path] = []
    for name in REQUIRED_FILES:
        backup = install_file(source_root / name, dest / name, suffix)
        if backup is not None:
            backups.append(backup)
        installed.append(dest / name)

    print(f"[APPLIED] installed={len(installed)} backups={len(backups)}")
    for backup in backups:
        print(f"  backup: {backup}")
    print("Run a one-page render smoke test before any bulk re-render.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
