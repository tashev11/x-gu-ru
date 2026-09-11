from __future__ import annotations

import importlib.util
import os
import tempfile
import time
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "server-opt" / "prune_releases.py"
SPEC = importlib.util.spec_from_file_location("xgu_prune_releases", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot load prune module from {MODULE_PATH}")
prune_releases = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(prune_releases)


class PruneReleaseTests(unittest.TestCase):
    def _release(self, root: Path, name: str, age_seconds: int) -> Path:
        path = root / name
        path.mkdir()
        stamp = time.time() - age_seconds
        os.utime(path, (stamp, stamp))
        return path

    def test_active_release_is_never_in_delete_plan(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "releases"
            root.mkdir()
            newest = self._release(root, "r3", 10)
            active = self._release(root, "r2", 20)
            oldest = self._release(root, "r1", 30)
            current = Path(temp) / "current"
            current.symlink_to(active)

            protected, delete = prune_releases.build_plan(root, current, keep=1)

            self.assertIn(newest.resolve(), protected)
            self.assertIn(active.resolve(), protected)
            self.assertNotIn(active.resolve(), delete)
            self.assertEqual(delete, [oldest.resolve()])

    def test_pre_bootstrap_backup_is_protected_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "releases"
            root.mkdir()
            active = self._release(root, "r3", 10)
            normal_old = self._release(root, "r1", 30)
            bootstrap_backup = self._release(root, "pre-bootstrap-20260910-180000", 9999)
            current = Path(temp) / "current"
            current.symlink_to(active)

            protected, delete = prune_releases.build_plan(root, current, keep=1)

            self.assertIn(bootstrap_backup.resolve(), protected)
            self.assertNotIn(bootstrap_backup.resolve(), delete)
            self.assertIn(normal_old.resolve(), delete)

    def test_bootstrap_backup_can_only_enter_plan_with_explicit_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "releases"
            root.mkdir()
            active = self._release(root, "r3", 10)
            bootstrap_backup = self._release(root, "pre-bootstrap-20260910-180000", 9999)
            current = Path(temp) / "current"
            current.symlink_to(active)

            protected, delete = prune_releases.build_plan(
                root,
                current,
                keep=1,
                protect_bootstrap_backups=False,
            )

            self.assertNotIn(bootstrap_backup.resolve(), protected)
            self.assertIn(bootstrap_backup.resolve(), delete)

    def test_keep_must_be_positive(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = root / "current"
            with self.assertRaises(ValueError):
                prune_releases.build_plan(root, current, keep=0)

    def test_safe_delete_rejects_nested_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "releases"
            nested = root / "release" / "nested"
            nested.mkdir(parents=True)
            with self.assertRaises(RuntimeError):
                prune_releases._safe_delete(nested, root)

    def test_nested_current_target_is_not_accepted_as_active_release(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "releases"
            nested = root / "r1" / "nested"
            nested.mkdir(parents=True)
            current = Path(temp) / "current"
            current.symlink_to(nested)

            self.assertIsNone(prune_releases._current_release(current, root))

    def test_rechecks_current_immediately_before_delete(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "releases"
            root.mkdir()
            old_active = self._release(root, "r3", 10)
            candidate = self._release(root, "r1", 30)
            current = Path(temp) / "current"
            current.symlink_to(old_active)

            current.unlink()
            current.symlink_to(candidate)

            with self.assertRaises(RuntimeError):
                prune_releases._delete_if_still_inactive(candidate, root, current)
            self.assertTrue(candidate.is_dir())

    def test_refuses_delete_when_current_cannot_be_proven(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "releases"
            root.mkdir()
            candidate = self._release(root, "r1", 30)
            current = Path(temp) / "current"

            with self.assertRaises(RuntimeError):
                prune_releases._delete_if_still_inactive(candidate, root, current)
            self.assertTrue(candidate.is_dir())


if __name__ == "__main__":
    unittest.main()
