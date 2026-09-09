#!/usr/bin/env python3
"""Atomically switch x-gu.ru to a validated release directory.

The command is dry-run by default. ``--apply`` is required to replace the
``current`` symlink. It refuses to replace a real directory: migrate the
existing deployment to a symlink intentionally before using this helper.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path


def _required_paths(release: Path) -> list[Path]:
    return [
        release / "index.html",
        release / "robots.txt",
        release / "sitemap.xml",
    ]


def validate_release(release: Path) -> list[str]:
    errors: list[str] = []
    if not release.is_dir():
        return [f"release directory not found: {release}"]

    for path in _required_paths(release):
        if not path.is_file():
            errors.append(f"required release file missing: {path.name}")
        elif path.stat().st_size == 0:
            errors.append(f"required release file is empty: {path.name}")

    index = release / "index.html"
    if index.is_file():
        text = index.read_text(encoding="utf-8", errors="ignore")
        if "<html" not in text.lower():
            errors.append("index.html does not look like HTML")
        if "<title" not in text.lower():
            errors.append("index.html has no title")

    sitemap = release / "sitemap.xml"
    if sitemap.is_file():
        text = sitemap.read_text(encoding="utf-8", errors="ignore")
        if "<sitemapindex" not in text and "<urlset" not in text:
            errors.append("sitemap.xml is not a sitemap index/urlset")

    return errors


def _current_target(current: Path) -> Path | None:
    if not current.exists() and not current.is_symlink():
        return None
    if not current.is_symlink():
        raise RuntimeError(
            f"{current} is not a symlink; refusing to replace a real directory/file atomically"
        )
    raw = Path(os.readlink(current))
    if raw.is_absolute():
        return raw
    return (current.parent / raw).resolve()


def switch_release(current: Path, release: Path) -> Path | None:
    previous = _current_target(current)
    current.parent.mkdir(parents=True, exist_ok=True)

    temp_link = current.parent / f".{current.name}.next.{os.getpid()}.{int(time.time())}"
    try:
        os.symlink(str(release), temp_link)
        os.replace(temp_link, current)
    finally:
        if temp_link.is_symlink():
            temp_link.unlink()
    return previous


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("release_dir", type=Path, help="fully built release directory")
    parser.add_argument(
        "--current",
        type=Path,
        default=Path("/var/www/x-gu.ru/current"),
        help="current release symlink",
    )
    parser.add_argument("--apply", action="store_true", help="atomically switch current to release_dir")
    args = parser.parse_args()

    release = args.release_dir.resolve()
    current = args.current

    errors = validate_release(release)
    if errors:
        print("Release validation: FAIL", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 2

    try:
        previous = _current_target(current)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 3

    if previous is not None and previous.resolve() == release:
        print(f"current already points to release: {release}")
        return 0

    print("Release validation: OK")
    print(f"release:  {release}")
    print(f"current:  {current}")
    print(f"previous: {previous or '(none)'}")

    if not args.apply:
        print("[DRY-RUN] Symlink not changed. Re-run with --apply after release validation.")
        return 0

    previous = switch_release(current, release)
    print(f"[APPLIED] {current} -> {release}")
    if previous is not None:
        print(f"rollback target: {previous}")
        print(f"rollback command: {sys.executable} {Path(__file__).name} {previous} --current {current} --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
