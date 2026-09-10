from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from release_safety import (
    active_release,
    atomic_replace_text,
    mutation_target_error,
    release_operation_lock,
)


class ReleaseSafetyTests(unittest.TestCase):
    def test_isolated_release_candidate_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            releases = root / "releases"
            releases.mkdir()
            candidate = releases / "candidate"
            candidate.mkdir()
            self.assertIsNone(mutation_target_error(candidate, current=root / "current", releases_root=releases))

    def test_symlink_candidate_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            releases = root / "releases"
            releases.mkdir()
            real = releases / "real"
            real.mkdir()
            alias = releases / "alias"
            alias.symlink_to(real)
            error = mutation_target_error(alias, current=root / "current", releases_root=releases)
            self.assertIsNotNone(error)
            self.assertIn("must not be a symlink", error)

    def test_active_release_is_rejected_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            releases = root / "releases"
            releases.mkdir()
            active = releases / "active"
            active.mkdir()
            current = root / "current"
            current.symlink_to(active)
            self.assertEqual(active_release(current), active.resolve())
            self.assertIsNotNone(mutation_target_error(active, current=current, releases_root=releases))
            self.assertIsNone(
                mutation_target_error(active, current=current, releases_root=releases, allow_active_current=True)
            )

    def test_outside_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            releases = root / "releases"
            releases.mkdir()
            outside = root / "outside"
            outside.mkdir()
            error = mutation_target_error(outside, current=root / "current", releases_root=releases)
            self.assertIsNotNone(error)
            self.assertIn("direct child", error)

    def test_missing_target_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            error = mutation_target_error(root / "missing", current=root / "current", releases_root=root / "releases")
            self.assertIsNotNone(error)
            self.assertIn("not a directory", error)

    def test_atomic_replace_preserves_existing_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "index.html"
            path.write_text("old", encoding="utf-8")
            os.chmod(path, 0o640)
            atomic_replace_text(path, "new")
            self.assertEqual(path.read_text(encoding="utf-8"), "new")
            self.assertEqual(path.stat().st_mode & 0o777, 0o640)

    def test_atomic_replace_can_create_new_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "index.html"
            atomic_replace_text(path, "created")
            self.assertEqual(path.read_text(encoding="utf-8"), "created")
            self.assertEqual(path.stat().st_mode & 0o777, 0o644)

    def test_atomic_replace_rejects_symlink_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            real = root / "real.txt"
            real.write_text("keep", encoding="utf-8")
            link = root / "link.txt"
            link.symlink_to(real)
            with self.assertRaises(RuntimeError):
                atomic_replace_text(link, "replace")
            self.assertEqual(real.read_text(encoding="utf-8"), "keep")

    def test_atomic_replace_does_not_create_missing_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "missing" / "index.html"
            with self.assertRaises(FileNotFoundError):
                atomic_replace_text(path, "nope")
            self.assertFalse(path.exists())

    def test_release_operation_lock_rejects_second_holder(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            lock = Path(temp) / ".release-operation.lock"
            with release_operation_lock(lock):
                with self.assertRaises(RuntimeError):
                    with release_operation_lock(lock):
                        self.fail("second release lock holder must never enter")
            with release_operation_lock(lock):
                self.assertTrue(lock.is_file())

    def test_release_operation_lock_rejects_symlink_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            victim = root / "victim.txt"
            victim.write_text("do-not-truncate", encoding="utf-8")
            lock = root / ".release-operation.lock"
            lock.symlink_to(victim)
            with self.assertRaises(RuntimeError):
                with release_operation_lock(lock):
                    pass
            self.assertEqual(victim.read_text(encoding="utf-8"), "do-not-truncate")

    def test_release_operation_lock_does_not_create_missing_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            lock = Path(temp) / "missing" / ".release-operation.lock"
            with self.assertRaises(FileNotFoundError):
                with release_operation_lock(lock):
                    pass
            self.assertFalse(lock.parent.exists())


if __name__ == "__main__":
    unittest.main()
