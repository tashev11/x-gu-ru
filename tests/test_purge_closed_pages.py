from __future__ import annotations

import hashlib
import importlib.util
import json
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
    def _contract(self, root: Path, whitelist_text: str = "") -> tuple[Path, Path]:
        whitelist = root / purge.WHITELIST_FILENAME
        whitelist.write_text(whitelist_text, encoding="utf-8")
        manifest = root / purge.KEEP_FILENAME
        manifest.write_text(
            json.dumps(
                {
                    "open_cities": ["moskva"],
                    "open_services": ["seo-audit-saita"],
                    "policy_source": "reviewed",
                    "policy_sha256": "a" * 64,
                    "whitelist_source": "reviewed",
                    "whitelist_sha256": hashlib.sha256(whitelist_text.encode("utf-8")).hexdigest(),
                }
            ),
            encoding="utf-8",
        )
        return manifest, whitelist

    def test_release_contract_accepts_matching_whitelist_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _manifest, whitelist = self._contract(root, "https://x-gu.ru/moskva/\n")
            resolved, errors = purge.validate_release_contract(root)
            self.assertEqual(errors, [])
            self.assertEqual(resolved, whitelist)

    def test_release_contract_rejects_whitelist_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _manifest, whitelist = self._contract(root)
            whitelist.write_text("https://x-gu.ru/moskva/\n", encoding="utf-8")
            _resolved, errors = purge.validate_release_contract(root)
            self.assertTrue(any("SHA-256 mismatch" in error for error in errors))

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
