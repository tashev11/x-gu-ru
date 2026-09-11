from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "server-opt" / "link_graph_cluster_audit.py"
SPEC = importlib.util.spec_from_file_location("xgu_link_graph_cluster_audit", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot load link graph audit from {MODULE_PATH}")
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)

BASE = "https://x-gu.ru"


def html_page(label: str, links: list[str], *, body_label: str | None = None) -> str:
    body = " ".join([f"контент-{body_label or label}"] * 320)
    anchors = " ".join(f'<a href="{href}">{href}</a>' for href in links)
    return f"""<!doctype html><html lang="ru"><head>
<title>{label}</title>
<meta name="description" content="Описание {label}">
<link rel="canonical" href="{BASE}/">
</head><body><h1>{label}</h1>{anchors}<p>{body}</p></body></html>"""


class LinkGraphClusterAuditTests(unittest.TestCase):
    def _release(self, *, connect_tver: bool = True) -> tuple[tempfile.TemporaryDirectory, Path]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / "index.html").write_text(html_page("home", ["/moskva/"]), encoding="utf-8")

        for city in ("moskva", "tver"):
            city_dir = root / city
            city_dir.mkdir()
            service_dir = city_dir / "seo-audit-saita"
            service_dir.mkdir()

        moskva_links = ["/moskva/seo-audit-saita/"]
        (root / "moskva" / "index.html").write_text(html_page("moskva", moskva_links), encoding="utf-8")
        service_links = ["/tver/"] if connect_tver else []
        (root / "moskva" / "seo-audit-saita" / "index.html").write_text(
            html_page("seo-moskva", service_links, body_label="same-service"), encoding="utf-8"
        )
        (root / "tver" / "index.html").write_text(
            html_page("tver", ["/tver/seo-audit-saita/"]), encoding="utf-8"
        )
        (root / "tver" / "seo-audit-saita" / "index.html").write_text(
            html_page("seo-tver", [], body_label="same-service"), encoding="utf-8"
        )

        whitelist = root / ".xgu-whitelist.txt"
        whitelist.write_text("", encoding="utf-8")
        (root / ".xgu-index-keep.json").write_text(
            json.dumps(
                {
                    "policy_version": 1,
                    "open_cities": ["moskva", "tver"],
                    "open_services": ["seo-audit-saita"],
                    "policy_source": "test",
                    "policy_sha256": "a" * 64,
                    "whitelist_source": "test",
                    "whitelist_sha256": hashlib.sha256(b"").hexdigest(),
                }
            ),
            encoding="utf-8",
        )
        return temp, root

    def test_computes_crawl_depth_from_home(self) -> None:
        temp, root = self._release(connect_tver=True)
        self.addCleanup(temp.cleanup)
        audit = mod.run_audit(root, deep_threshold=3)
        self.assertEqual(audit["indexable_pages"], 5)
        self.assertEqual(audit["reachable_from_home"], 5)
        self.assertEqual(audit["unreachable_from_home_pages"], 0)
        self.assertEqual(audit["max_crawl_depth"], 4)
        self.assertEqual(audit["deep_indexable_pages"], 1)
        self.assertEqual(audit["crawl_depth_distribution"], {"0": 1, "1": 1, "2": 1, "3": 1, "4": 1})

    def test_detects_pages_in_unreachable_internal_cluster(self) -> None:
        temp, root = self._release(connect_tver=False)
        self.addCleanup(temp.cleanup)
        audit = mod.run_audit(root)
        self.assertEqual(audit["unreachable_from_home_pages"], 2)
        self.assertIn(f"{BASE}/tver/", audit["unreachable_from_home_urls"])
        self.assertIn(f"{BASE}/tver/seo-audit-saita/", audit["unreachable_from_home_urls"])
        breaches = mod.evaluate(audit)
        self.assertTrue(any("unreachable_from_home_pages" in item for item in breaches))

    def test_same_service_cross_city_similarity_is_reported(self) -> None:
        temp, root = self._release(connect_tver=True)
        self.addCleanup(temp.cleanup)
        audit = mod.run_audit(root, max_simhash_distance=6)
        self.assertEqual(audit["same_service_exact_duplicate_pages"], 0)
        self.assertGreaterEqual(audit["same_service_near_duplicate_pages"], 2)
        cluster = next(item for item in audit["service_similarity_clusters"] if item["service"] == "seo-audit-saita")
        self.assertGreaterEqual(cluster["near_duplicate_pages"], 2)

    def test_invalid_deep_threshold_is_rejected(self) -> None:
        temp, root = self._release()
        self.addCleanup(temp.cleanup)
        with self.assertRaises(ValueError):
            mod.run_audit(root, deep_threshold=0)


if __name__ == "__main__":
    unittest.main()
