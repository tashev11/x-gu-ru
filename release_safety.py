"""Shared path and write guards for x-gu.ru production-mutating tools."""
from __future__ import annotations

import os
import time
from pathlib import Path


DEFAULT_CURRENT = Path("/var/www/x-gu.ru/current")
DEFAULT_RELEASES_ROOT = Path("/var/www/x-gu.ru/releases")


def active_release(current: Path) -> Path | None:
    """Return the resolved active target only when ``current`` resolves cleanly."""
    if not current.exists() and not current.is_symlink():
        return None
    try:
        return current.resolve(strict=True)
    except OSError:
        return None


def mutation_target_error(
    target: Path,
    *,
    current: Path = DEFAULT_CURRENT,
    releases_root: Path = DEFAULT_RELEASES_ROOT,
    allow_active_current: bool = False,
) -> str | None:
    """Validate a bulk-write target.

    Normal writes must target a direct child of ``releases_root`` and must not
    be the active ``current`` target. The active target can only be allowed by
    an explicit emergency override.
    """
    if not target.is_dir():
        return f"mutation target is not a directory: {target}"

    resolved = target.resolve()
    releases = releases_root.resolve()
    active = active_release(current)

    if active is not None and resolved == active:
        if allow_active_current:
            return None
        return (
            "refusing to mutate the active current release; use an isolated release candidate. "
            "An active-current override is for emergency recovery only."
        )

    if resolved.parent != releases:
        return f"mutation target must be a direct child of releases root: target={resolved} releases_root={releases}"
    return None


def atomic_replace_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    """Replace one text file atomically while preserving its permission bits."""
    mode = path.stat().st_mode & 0o777
    temp = path.with_name(f".{path.name}.next.{os.getpid()}.{time.time_ns()}")
    try:
        temp.write_text(text, encoding=encoding)
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()
