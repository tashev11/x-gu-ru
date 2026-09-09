from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from server_opt_import import load_deploy_module


deploy_release = load_deploy_module()


class DeployReleaseTests(unittest.TestCase):
    def _release(self, root: Path, name: str) -> Path:
        release = root / name
        release.mkdir()
        (release / "index.html").write_text("<html><head><title>ok</title></head></html>", encoding="utf-8")
        (release / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
        (release / "sitemap.xml").write_text(
            '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>',
            encoding="utf-8",
        )
        return release

    def test_validate_release_requires_core_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            release = Path(temp) / "broken"
            release.mkdir()
            errors = deploy_release.validate_release(release)
            self.assertGreaterEqual(len(errors), 3)

    def test_atomic_switch_returns_previous_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old = self._release(root, "old")
            new = self._release(root, "new")
            current = root / "current"
            current.symlink_to(old)

            previous = deploy_release.switch_release(current, new)

            self.assertEqual(previous, old.resolve())
            self.assertTrue(current.is_symlink())
            self.assertEqual(current.resolve(), new.resolve())

    def test_refuses_real_current_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            current = root / "current"
            current.mkdir()
            with self.assertRaises(RuntimeError):
                deploy_release._current_target(current)


if __name__ == "__main__":
    unittest.main()
