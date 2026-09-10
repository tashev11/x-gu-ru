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
        (current / "index.html").write_text("<html><title>legacy</title></html>", encoding="utf-8")
        (current / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
        (current / "sitemap.xml").write_text("<urlset></urlset>", encoding="utf-8")
        return current

    def _target(self, releases: Path, name: str = "candidate") -> Path:
        releases.mkdir(exist_ok=True)
        target = releases / name
        target.mkdir()
        (target / "index.html").write_text("<html><title>candidate</title></html>", encoding="utf-8")
        (target / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
        (target / "sitemap.xml").write_text("<urlset></urlset>", encoding="utf-8")
        (target / bootstrap_mod.KEEP_FILENAME).write_text("{}", encoding="utf-8")
        (target / bootstrap_mod.WHITELIST_FILENAME).write_text("\n", encoding="utf-8")
        return target

    def test_validation_accepts_real_current_and_prepared_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = self._current(root)
            releases = root / "releases"
            target = self._target(releases)
            backup = releases / "pre-bootstrap-test"

            errors = bootstrap_mod._validate_layout(current, releases, target, backup)

            self.assertEqual(errors, [])

    def test_validation_rejects_missing_legacy_core_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = self._current(root)
            (current / "robots.txt").unlink()
            releases = root / "releases"
            target = self._target(releases)

            errors = bootstrap_mod._validate_layout(current, releases, target, releases / "pre-bootstrap-test")

            self.assertTrue(any("legacy current" in error and "robots.txt" in error for error in errors))

    def test_validation_rejects_target_without_release_contract(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = self._current(root)
            releases = root / "releases"
            target = self._target(releases)
            (target / bootstrap_mod.KEEP_FILENAME).unlink()

            errors = bootstrap_mod._validate_layout(current, releases, target, releases / "pre-bootstrap-test")

            self.assertTrue(any(bootstrap_mod.KEEP_FILENAME in error for error in errors))

    def test_validation_rejects_already_symlinked_current(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            releases = root / "releases"
            target = self._target(releases)
            active = releases / "active"
            active.mkdir()
            current = root / "current"
            current.symlink_to(active)

            errors = bootstrap_mod._validate_layout(current, releases, target, releases / "pre-bootstrap-test")

            self.assertTrue(any("already a symlink" in error for error in errors))

    def test_bootstrap_archives_legacy_current_and_switches_to_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = self._current(root)
            releases = root / "releases"
            target = self._target(releases)
            backup = releases / "pre-bootstrap-test"

            bootstrap_mod.bootstrap(current, releases, target, backup)

            self.assertTrue(current.is_symlink())
            self.assertEqual(current.resolve(), target.resolve())
            self.assertTrue((target / "index.html").is_file())
            self.assertEqual((target / "index.html").read_text(encoding="utf-8"), "<html><title>candidate</title></html>")
            self.assertTrue((backup / "index.html").is_file())
            self.assertEqual((backup / "index.html").read_text(encoding="utf-8"), "<html><title>legacy</title></html>")

    def test_symlink_install_failure_restores_legacy_current(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = self._current(root)
            releases = root / "releases"
            target = self._target(releases)
            backup = releases / "pre-bootstrap-test"

            with patch.object(bootstrap_mod.os, "replace", side_effect=OSError("simulated replace failure")):
                with self.assertRaises(OSError):
                    bootstrap_mod.bootstrap(current, releases, target, backup)

            self.assertTrue(current.is_dir())
            self.assertFalse(current.is_symlink())
            self.assertEqual((current / "index.html").read_text(encoding="utf-8"), "<html><title>legacy</title></html>")
            self.assertFalse(backup.exists())
            self.assertTrue(target.is_dir())
            self.assertEqual((target / "index.html").read_text(encoding="utf-8"), "<html><title>candidate</title></html>")


if __name__ == "__main__":
    unittest.main()
