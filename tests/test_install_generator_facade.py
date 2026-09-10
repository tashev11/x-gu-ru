from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "server-opt" / "install_generator_facade.py"
SPEC = importlib.util.spec_from_file_location("xgu_install_generator", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot load installer module from {MODULE_PATH}")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


class InstallGeneratorFacadeTests(unittest.TestCase):
    def _sources(self, root: Path) -> Path:
        source = root / "source"
        source.mkdir()
        for name in installer.REQUIRED_FILES:
            (source / name).write_text(f"VALUE = 'new:{name}'\n", encoding="utf-8")
        return source

    def _dest(self, root: Path) -> Path:
        dest = root / "dest"
        dest.mkdir()
        for name in installer.REQUIRED_FILES:
            (dest / name).write_text(f"VALUE = 'old:{name}'\n", encoding="utf-8")
        return dest

    def test_installs_complete_set_and_keeps_backups(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self._sources(root)
            dest = self._dest(root)
            backups = installer.install_set(source, dest, ".bak.test")

            self.assertEqual(len(backups), len(installer.REQUIRED_FILES))
            for name in installer.REQUIRED_FILES:
                self.assertEqual((dest / name).read_text(encoding="utf-8"), f"VALUE = 'new:{name}'\n")
                self.assertEqual(
                    (dest / f"{name}.bak.test").read_text(encoding="utf-8"),
                    f"VALUE = 'old:{name}'\n",
                )

    def test_partial_replace_failure_rolls_back_live_targets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self._sources(root)
            dest = self._dest(root)
            real_replace = installer.os.replace
            live_replace_calls = 0

            def flaky_replace(src: Path, dst: Path) -> None:
                nonlocal live_replace_calls
                if ".rollback." not in Path(src).name:
                    live_replace_calls += 1
                    if live_replace_calls == 2:
                        raise OSError("simulated replace failure")
                real_replace(src, dst)

            with mock.patch.object(installer.os, "replace", side_effect=flaky_replace):
                with self.assertRaises(OSError):
                    installer.install_set(source, dest, ".bak.test")

            for name in installer.REQUIRED_FILES:
                self.assertEqual((dest / name).read_text(encoding="utf-8"), f"VALUE = 'old:{name}'\n")

    def test_invalid_python_is_rejected_before_live_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = self._sources(root)
            dest = self._dest(root)
            broken = source / "content_generator.py"
            broken.write_text("def broken(:\n", encoding="utf-8")

            errors = installer.validate_sources(source)
            self.assertTrue(any("invalid Python source" in error for error in errors))

            with self.assertRaises(ValueError):
                installer.install_set(source, dest, ".bak.test")

            for name in installer.REQUIRED_FILES:
                self.assertEqual((dest / name).read_text(encoding="utf-8"), f"VALUE = 'old:{name}'\n")


if __name__ == "__main__":
    unittest.main()
