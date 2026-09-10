from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "server-opt" / "bootstrap_release_layout.py"
SPEC = importlib.util.spec_from_file_location("xgu_bootstrap_release_layout", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot load bootstrap module from {MODULE_PATH}")
bootstrap_mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bootstrap_mod)


class BootstrapReleaseLayoutTests(unittest.TestCase):
    def _current(self, root: Path) -> Path:
        current = root / "current"
        current.mkdir()
        (current / "index.html").write_text("<html><title>x</title></html>", encoding="utf-8")
        (current / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
        (current / "sitemap.xml").write_text("<urlset></urlset>", encoding="utf-8")
        return current

    def test_validation_accepts_real_complete_current(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = self._current(root)
            releases = root / "releases"
            release = releases / "bootstrap-test"

            errors = bootstrap_mod._validate_layout(current, releases, release)

            self.assertEqual(errors, [])

    def test_validation_rejects_missing_core_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = self._current(root)
            (current / "robots.txt").unlink()
            releases = root / "releases"

            errors = bootstrap_mod._validate_layout(current, releases, releases / "bootstrap-test")

            self.assertTrue(any("robots.txt" in error for error in errors))

    def test_validation_rejects_already_symlinked_current(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            releases = root / "releases"
            releases.mkdir()
            active = releases / "active"
            active.mkdir()
            current = root / "current"
            current.symlink_to(active)

            errors = bootstrap_mod._validate_layout(current, releases, releases / "bootstrap-test")

            self.assertTrue(any("already a symlink" in error for error in errors))

    def test_bootstrap_moves_directory_and_installs_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = self._current(root)
            releases = root / "releases"
            release = releases / "bootstrap-test"

            bootstrap_mod.bootstrap(current, releases, release)

            self.assertTrue(current.is_symlink())
            self.assertEqual(current.resolve(), release.resolve())
            self.assertTrue((release / "index.html").is_file())
            self.assertTrue((release / "robots.txt").is_file())
            self.assertTrue((release / "sitemap.xml").is_file())

    def test_symlink_install_failure_rolls_directory_back(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = self._current(root)
            releases = root / "releases"
            release = releases / "bootstrap-test"

            with patch.object(bootstrap_mod.os, "replace", side_effect=OSError("simulated replace failure")):
                with self.assertRaises(OSError):
                    bootstrap_mod.bootstrap(current, releases, release)

            self.assertTrue(current.is_dir())
            self.assertFalse(current.is_symlink())
            self.assertTrue((current / "index.html").is_file())
            self.assertFalse(release.exists())


if __name__ == "__main__":
    unittest.main()
