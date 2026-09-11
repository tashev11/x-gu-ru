"""Deterministic integrity metadata for immutable x-gu.ru release candidates."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from release_safety import atomic_replace_text


RELEASE_METADATA_FILENAME = ".xgu-release.json"
RELEASE_METADATA_VERSION = 1
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def normalize_revision(value: str) -> str:
    revision = value.strip().lower()
    if not _SHA_RE.fullmatch(revision):
        raise ValueError("tooling revision must be a full 40-character lowercase/uppercase Git SHA-1")
    return revision


def release_files(root: Path) -> list[Path]:
    """Return regular files included in the deterministic release fingerprint.

    Symlinks are rejected: a deployable static release must be self-contained
    and must not depend on mutable paths outside its directory.
    """
    root = root.resolve()
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"release contains symlink: {path.relative_to(root)}")
        if not path.is_file():
            continue
        if path.name == RELEASE_METADATA_FILENAME:
            continue
        files.append(path)
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def compute_release_digest(root: Path) -> tuple[str, int, int]:
    """Hash relative paths, lengths and bytes of every regular release file."""
    root = root.resolve()
    digest = hashlib.sha256()
    total_bytes = 0
    files = release_files(root)
    for path in files:
        rel = path.relative_to(root).as_posix().encode("utf-8")
        size = path.stat().st_size
        digest.update(len(rel).to_bytes(4, "big"))
        digest.update(rel)
        digest.update(size.to_bytes(8, "big"))
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                total_bytes += len(chunk)
    return digest.hexdigest(), len(files), total_bytes


def build_release_metadata(
    root: Path,
    *,
    tooling_revision: str,
    finalized_at: str,
    source_release: str = "",
) -> dict:
    revision = normalize_revision(tooling_revision)
    content_sha256, file_count, total_bytes = compute_release_digest(root)
    return {
        "contract_version": RELEASE_METADATA_VERSION,
        "tooling_revision": revision,
        "finalized_at": finalized_at,
        "source_release": source_release.strip(),
        "content_sha256": content_sha256,
        "file_count": file_count,
        "total_bytes": total_bytes,
    }


def write_release_metadata(root: Path, payload: dict) -> Path:
    path = root.resolve() / RELEASE_METADATA_FILENAME
    atomic_replace_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return path


def verify_release_metadata(root: Path) -> tuple[dict | None, list[str]]:
    root = root.resolve()
    path = root / RELEASE_METADATA_FILENAME
    if not path.is_file():
        return None, [f"release integrity metadata missing: {path}"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8", errors="strict"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return None, [f"release integrity metadata is invalid JSON: {exc}"]
    if not isinstance(payload, dict):
        return None, ["release integrity metadata root must be an object"]

    errors: list[str] = []
    if payload.get("contract_version") != RELEASE_METADATA_VERSION:
        errors.append(
            f"unsupported release contract_version: {payload.get('contract_version')!r}; expected {RELEASE_METADATA_VERSION}"
        )
    try:
        normalize_revision(str(payload.get("tooling_revision") or ""))
    except ValueError as exc:
        errors.append(str(exc))
    if not str(payload.get("finalized_at") or "").strip():
        errors.append("release integrity metadata has no finalized_at")

    expected = str(payload.get("content_sha256") or "").strip().lower()
    if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
        errors.append("release integrity metadata has invalid content_sha256")
        return payload, errors

    try:
        actual, count, total_bytes = compute_release_digest(root)
    except (OSError, ValueError) as exc:
        errors.append(f"cannot fingerprint release: {exc}")
        return payload, errors

    if actual != expected:
        errors.append(f"release content SHA-256 mismatch: manifest={expected} actual={actual}")
    if payload.get("file_count") != count:
        errors.append(f"release file_count mismatch: manifest={payload.get('file_count')} actual={count}")
    if payload.get("total_bytes") != total_bytes:
        errors.append(f"release total_bytes mismatch: manifest={payload.get('total_bytes')} actual={total_bytes}")
    return payload, errors
