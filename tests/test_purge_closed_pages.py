from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "server-opt" / "purge_closed_pages.py"
SPEC = importlib.util.spec_from_file_location("xgu_purge_closed", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot load purge module from {MODULE_PATH}")
purge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(purge)


def html(*, noindex: bool) -> str:
    robots = '<meta name="robots" content="noindex, follow">' if noindex else '<meta name="robots" content="index,follow">'
    return f"<html><head>{robots}</head><body>page</body></html>"


class PurgeClosedPagesTests(unittest.TestCase):
    def test_missing_index_directory_is_never_delete_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            city = root / "moskva"
            city.mkdir()
            (city / "index.html").write_text(html(noindex=False), encoding="utf-8")
            missing = city / "missing-service"
            missing.mkdir()

            plan, stats = purge.build_plan(root, {"https://x-gu.ru/moskva/"})

            self.assertEqual(plan, [])
            self.assertEqual(stats["missing_index"], 1)

    def test_only_explicit_noindex_service_is_delete_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            city = root / "moskva"
            city.mkdir()
            (city / "index.html").write_text(html(noindex=False), encoding="utf-8")
            closed = city / "closed"
            closed.mkdir()
            (closed / "index.html").write_text(html(noindex=True), encoding="utf-8")
            open_page = city / "open"
            open_page.mkdir()
            (open_page / "index.html").write_text(html(noindex=False), encoding="utf-8")

            protected = {"https://x-gu.ru/moskva/", "https://x-gu.ru/moskva/open/"}
            plan, stats = purge.build_plan(root, protected)

            self.assertEqual(plan, [(closed, "https://x-gu.ru/moskva/closed/")])
            self.assertGreaterEqual(stats["kept"], 1)

    def test_noindex_city_hub_can_be_removed_only_without_protected_children(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            city = root / "tula"
            city.mkdir()
            (city / "index.html").write_text(html(noindex=True), encoding="utf-8")
            child = city / "closed"
            child.mkdir()
            (child / "index.html").write_text(html(noindex=True), encoding="utf-8")

            plan, _stats = purge.build_plan(root, set())
            paths = [path for path, _url in plan]
            self.assertIn(child, paths)
            self.assertIn(city, paths)

    def test_safe_delete_refuses_path_outside_release(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            root = base / "release"
            root.mkdir()
            outside = base / "outside"
            outside.mkdir()
            with self.assertRaises(RuntimeError):
                purge._safe_delete(outside, root)
            self.assertTrue(outside.is_dir())


if __name__ == "__main__":
    unittest.main()
