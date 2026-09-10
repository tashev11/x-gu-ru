from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from release_safety import active_release, mutation_target_error


class ReleaseSafetyTests(unittest.TestCase):
    def test_isolated_release_candidate_is_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            releases = root / "releases"
            releases.mkdir()
            candidate = releases / "candidate"
            candidate.mkdir()
            current = root / "current"

            self.assertIsNone(
                mutation_target_error(candidate, current=current, releases_root=releases)
            )

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
            self.assertIsNotNone(
                mutation_target_error(active, current=current, releases_root=releases)
            )
            self.assertIsNone(
                mutation_target_error(
                    active,
                    current=current,
                    releases_root=releases,
                    allow_active_current=True,
                )
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
            error = mutation_target_error(
                root / "missing",
                current=root / "current",
                releases_root=root / "releases",
            )
            self.assertIsNotNone(error)
            self.assertIn("not a directory", error)


if __name__ == "__main__":
    unittest.main()
