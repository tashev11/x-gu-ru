from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "server-opt" / "deploy_release.py"
SPEC = importlib.util.spec_from_file_location("xgu_deploy_release", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot load deploy module from {MODULE_PATH}")
deploy_release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy_release)


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
