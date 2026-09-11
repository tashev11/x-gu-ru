"""Shared path, write and concurrency guards for x-gu.ru production tooling."""
from __future__ import annotations

import fcntl
import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, TextIO


DEFAULT_CURRENT = Path("/var/www/x-gu.ru/current")
DEFAULT_RELEASES_ROOT = Path("/var/www/x-gu.ru/releases")
DEFAULT_RELEASE_LOCK = Path("/var/www/x-gu.ru/.release-operation.lock")
FINALIZED_MARKER = ".xgu-release.json"


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

    Normal writes must target a real, unfinalized direct child of
    ``releases_root``. A finalized candidate is immutable: remove/rebuild it
    deliberately rather than mutating files after its fingerprint was signed.
    """
    if target.is_symlink():
        return f"mutation target must not be a symlink: {target}"
    if not target.is_dir():
        return f"mutation target is not a directory: {target}"

    resolved = target.resolve()
    releases = releases_root.resolve()
    active = active_release(current)

    if active is not None and resolved == active:
        if not allow_active_current:
            return (
                "refusing to mutate the active current release; use an isolated release candidate. "
                "An active-current override is for emergency recovery only."
            )

    if resolved.parent != releases:
        return f"mutation target must be a direct child of releases root: target={resolved} releases_root={releases}"

    finalized = resolved / FINALIZED_MARKER
    if finalized.exists() or finalized.is_symlink():
        return (
            f"mutation target is finalized and immutable: {finalized}. "
            "Create/rebuild a new candidate instead of modifying finalized content."
        )
    return None


@contextmanager
def release_operation_lock(lock_path: Path = DEFAULT_RELEASE_LOCK) -> Iterator[TextIO]:
    """Acquire the non-blocking host-wide lock for deploy/bootstrap/prune.

    ``O_NOFOLLOW`` rejects a symlink lock path before opening it. The lock file
    itself is persistent; flock ownership belongs to the open descriptor.
    """
    if not lock_path.parent.is_dir():
        raise FileNotFoundError(f"release lock parent does not exist: {lock_path.parent}")
    if lock_path.parent.is_symlink():
        raise RuntimeError(f"release lock parent must not be a symlink: {lock_path.parent}")

    flags = os.O_RDWR | os.O_CREAT | os.O_CLOEXEC
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise RuntimeError(f"cannot safely open release lock without following symlinks: {lock_path}: {exc}") from exc

    handle = os.fdopen(fd, "r+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise RuntimeError(f"another release operation already holds lock: {lock_path}") from exc

        handle.seek(0)
        handle.truncate(0)
        handle.write(f"pid={os.getpid()} acquired_at={int(time.time())}\n")
        handle.flush()
        os.fsync(handle.fileno())
        yield handle
    finally:
        if not handle.closed:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()


def atomic_replace_text(
    path: Path,
    text: str,
    *,
    encoding: str = "utf-8",
    default_mode: int = 0o644,
) -> None:
    """Atomically replace or create one non-symlink text file."""
    if not path.parent.is_dir():
        raise FileNotFoundError(f"parent directory does not exist: {path.parent}")
    if path.parent.is_symlink():
        raise RuntimeError(f"parent directory must not be a symlink: {path.parent}")
    if path.is_symlink():
        raise RuntimeError(f"refusing to replace symlink as a regular file: {path}")

    mode = (path.stat().st_mode & 0o777) if path.exists() else default_mode
    temp = path.with_name(f".{path.name}.next.{os.getpid()}.{time.time_ns()}")
    try:
        temp.write_text(text, encoding=encoding)
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()
