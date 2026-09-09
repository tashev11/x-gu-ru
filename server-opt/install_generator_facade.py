#!/usr/bin/env python3
"""Install the hardened generator files into the private ``app.services`` package.

Dry-run by default. With ``--apply`` the three files are staged first, existing
targets are backed up, and the set is replaced via ``os.replace``. If any
replacement fails, already-replaced targets are restored automatically.
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


def install_set(source_root: Path, dest: Path, backup_suffix: str) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    backups: dict[Path, Path | None] = {}
    staged: dict[Path, Path] = {}
    replaced: list[Path] = []

    try:
        # Stage every new file before mutating any live target.
        for name in REQUIRED_FILES:
            source = source_root / name
            target = dest / name
            temp = dest / f".{name}.next.{os.getpid()}"
            shutil.copy2(source, temp)
            staged[target] = temp

        # Snapshot every existing live target before the first replace.
        for target in staged:
            backup: Path | None = None
            if target.exists():
                backup = target.with_name(target.name + backup_suffix)
                shutil.copy2(target, backup)
            backups[target] = backup

        for target, temp in staged.items():
            os.replace(temp, target)
            replaced.append(target)

    except Exception:
        rollback_errors: list[str] = []
        for target in reversed(replaced):
            backup = backups.get(target)
            try:
                if backup is not None and backup.exists():
                    shutil.copy2(backup, target)
                elif target.exists():
                    target.unlink()
            except Exception as rollback_exc:  # noqa: BLE001
                rollback_errors.append(f"{target}: {rollback_exc}")
        if rollback_errors:
            raise RuntimeError(
                "generator install failed and rollback was incomplete: " + "; ".join(rollback_errors)
            )
        raise
    finally:
        for temp in staged.values():
            if temp.exists():
                temp.unlink()

    return [backup for backup in backups.values() if backup is not None]


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
    try:
        backups = install_set(source_root, dest, suffix)
    except Exception as exc:  # noqa: BLE001
        print(f"Install failed; rollback attempted: {exc}", file=sys.stderr)
        return 4

    print(f"[APPLIED] installed={len(REQUIRED_FILES)} backups={len(backups)}")
    for backup in backups:
        print(f"  backup: {backup}")
    print("Run a one-page render smoke test before any bulk re-render.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
