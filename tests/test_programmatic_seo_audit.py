from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "server-opt" / "programmatic_seo_audit.py"
SPEC = importlib.util.spec_from_file_location("xgu_programmatic_seo_audit", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot load programmatic SEO audit from {MODULE_PATH}")
audit_mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = audit_mod
SPEC.loader.exec_module(audit_mod)

BASE = "https://x-gu.ru"


def html_page(label: str, links: list[str], *, words: int = 320) -> str:
    body = " ".join([f"контент-{label}"] * words)
    anchors = " ".join(f'<a href="{href}">{href}</a>' for href in links)
    return f"""<!doctype html><html lang="ru"><head>
<title>{label}</title>
<meta name="description" content="Описание {label}">
<link rel="canonical" href="{BASE}/">
</head><body><h1>{label}</h1>{anchors}<p>{body}</p></body></html>"""


class ProgrammaticSeoAuditTests(unittest.TestCase):
    def _release(self, *, two_services: bool = False) -> tuple[tempfile.TemporaryDirectory, Path]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)

        services = ["seo-audit-saita"]
        if two_services:
            services.append("prodvizhenie-saita")

        (root / "index.html").write_text(html_page("home", ["/moskva/"]), encoding="utf-8")
        (root / "moskva").mkdir()
        city_links = [f"/moskva/{service}/" for service in services]
        (root / "moskva" / "index.html").write_text(html_page("moskva", city_links), encoding="utf-8")
        for service in services:
            path = root / "moskva" / service
            path.mkdir()
            (path / "index.html").write_text(html_page("same-service-body", ["/moskva/"]), encoding="utf-8")

        (root / "tula").mkdir()
        (root / "tula" / "index.html").write_text(html_page("tula", []), encoding="utf-8")

        whitelist = root / ".xgu-whitelist.txt"
        whitelist.write_text("", encoding="utf-8")
        whitelist_sha = hashlib.sha256(b"").hexdigest()
        (root / ".xgu-index-keep.json").write_text(
            json.dumps(
                {
                    "open_cities": ["moskva"],
                    "open_services": services,
                    "policy_source": "test",
                    "policy_sha256": "a" * 64,
                    "whitelist_source": "test",
                    "whitelist_sha256": whitelist_sha,
                }
            ),
            encoding="utf-8",
        )
        return temp, root

    def test_inventory_and_link_graph(self) -> None:
        temp, root = self._release()
        self.addCleanup(temp.cleanup)
        audit = audit_mod.run_audit(root, min_words=100)

        self.assertEqual(audit["physical_pages"], 4)
        self.assertEqual(audit["indexable_pages"], 3)
        self.assertEqual(audit["closed_pages"], 1)
        self.assertEqual(audit["orphan_indexable_pages"], 0)
        self.assertEqual(audit["indexable_links_to_closed"], 0)
        self.assertEqual(audit["thin_indexable_pages"], 0)

    def test_detects_thin_orphan_and_open_to_closed_link(self) -> None:
        temp, root = self._release()
        self.addCleanup(temp.cleanup)

        (root / "moskva" / "index.html").write_text(html_page("moskva", ["/tula/"]), encoding="utf-8")
        service = root / "moskva" / "seo-audit-saita" / "index.html"
        service.write_text(html_page("thin", [], words=10), encoding="utf-8")

        audit = audit_mod.run_audit(root, min_words=100)
        self.assertEqual(audit["thin_indexable_pages"], 1)
        self.assertEqual(audit["orphan_indexable_pages"], 1)
        self.assertEqual(audit["indexable_links_to_closed"], 1)

        breaches = audit_mod.evaluate(audit)
        self.assertTrue(any("thin_indexable_pages" in item for item in breaches))
        self.assertTrue(any("orphan_indexable_pages" in item for item in breaches))
        self.assertTrue(any("indexable_links_to_closed" in item for item in breaches))

    def test_detects_duplicate_indexable_service_pages(self) -> None:
        temp, root = self._release(two_services=True)
        self.addCleanup(temp.cleanup)

        audit = audit_mod.run_audit(root, min_words=100, max_simhash_distance=6)
        self.assertGreaterEqual(audit["exact_duplicate_pages"], 2)
        self.assertGreaterEqual(audit["near_duplicate_pages"], 2)

    def test_simhash_banding_finds_supported_distance(self) -> None:
        hashes = {
            "a": 0,
            "b": (1 << 0) | (1 << 8) | (1 << 16) | (1 << 24) | (1 << 32) | (1 << 40),
        }
        groups = audit_mod._near_duplicate_groups(hashes, max_distance=6)
        self.assertEqual(groups, [["a", "b"]])

    def test_simhash_distance_above_supported_limit_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            audit_mod._near_duplicate_groups({"a": 0}, max_distance=8)


if __name__ == "__main__":
    unittest.main()
