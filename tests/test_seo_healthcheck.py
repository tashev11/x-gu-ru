from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from seo_healthcheck import (
    RELEASE_KEEP_FILENAME,
    RELEASE_WHITELIST_FILENAME,
    evaluate,
    run_audit,
)


BASE = "https://example.test"


def page_html(
    *,
    canonical: str,
    noindex: bool = False,
    extra_html: str = "",
    jsonld: str = '{"@context":"https://schema.org","@type":"WebPage"}',
) -> str:
    robots = "noindex, follow" if noindex else "index,follow"
    body = " ".join(["контент"] * 260)
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <title>Достаточно длинный тестовый заголовок страницы</title>
  <meta content="Достаточно длинное описание тестовой страницы для корректной проверки SEO healthcheck без ложных срабатываний." name="description">
  <meta name="robots" content="{robots}">
  <link href="{canonical}" rel="canonical">
  <meta content="Тест" property="og:title">
  <meta content="Описание" property="og:description">
  <script type="application/ld+json">{jsonld}</script>
</head>
<body><h1>Тестовая страница</h1><p>{body}</p>{extra_html}</body>
</html>"""


class SeoHealthcheckPolicyTests(unittest.TestCase):
    def _fixture(self) -> tuple[Path, Path, Path, tempfile.TemporaryDirectory[str]]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / "moskva").mkdir()
        (root / "tula").mkdir()
        (root / "moskva" / "index.html").write_text(
            page_html(canonical=f"{BASE}/moskva/", extra_html='<a href="/tula/">Тула</a>'),
            encoding="utf-8",
        )
        (root / "tula" / "index.html").write_text(
            page_html(canonical=f"{BASE}/tula/", noindex=True), encoding="utf-8"
        )
        (root / "sitemap.xml").write_text(
            f'<?xml version="1.0"?><urlset><url><loc>{BASE}/moskva/</loc></url></urlset>',
            encoding="utf-8",
        )
        policy = root / "index_keep_config.json"
        policy.write_text(
            json.dumps({"open_cities": ["moskva"], "open_services": ["seo-audit-saita"]}),
            encoding="utf-8",
        )
        whitelist = root / "whitelist.txt"
        whitelist.write_text("", encoding="utf-8")
        return root, policy, whitelist, temp

    def _install_release_contract(self, root: Path) -> tuple[Path, Path]:
        whitelist = root / RELEASE_WHITELIST_FILENAME
        whitelist_text = ""
        whitelist.write_text(whitelist_text, encoding="utf-8")
        manifest = root / RELEASE_KEEP_FILENAME
        manifest.write_text(
            json.dumps(
                {
                    "open_cities": ["moskva"],
                    "open_services": ["seo-audit-saita"],
                    "policy_source": "/reviewed/index_policy.json",
                    "policy_sha256": "a" * 64,
                    "whitelist_source": "/reviewed/whitelist.txt",
                    "whitelist_sha256": hashlib.sha256(whitelist_text.encode("utf-8")).hexdigest(),
                }
            ),
            encoding="utf-8",
        )
        return manifest, whitelist

    def test_expected_open_and_closed_pages_are_clean(self) -> None:
        root, policy, whitelist, temp = self._fixture()
        self.addCleanup(temp.cleanup)
        audit = run_audit(root, base_url=BASE, keep_config=policy, whitelist=whitelist)
        stats = audit["stats"]
        self.assertTrue(audit["policy_loaded"])
        self.assertEqual(stats["unexpected_noindex_open"], 0)
        self.assertEqual(stats["unexpected_index_closed"], 0)
        self.assertEqual(stats["open_missing_sitemap"], 0)
        self.assertEqual(stats["closed_in_sitemap"], 0)
        self.assertEqual(stats["canonical_url_mismatch"], 0)
        self.assertEqual(stats["broken_internal_links"], 0)
        self.assertEqual(stats["invalid_jsonld_pages"], 0)
        self.assertEqual(stats["bad_sitemap_urls"], 0)
        self.assertEqual(stats["sitemap_orphan_urls"], 0)

    def test_release_contract_is_default_for_release_root(self) -> None:
        root, _policy, _whitelist, temp = self._fixture()
        self.addCleanup(temp.cleanup)
        manifest, whitelist = self._install_release_contract(root)
        audit = run_audit(root, base_url=BASE)
        self.assertEqual(Path(audit["keep_config"]), manifest)
        self.assertEqual(Path(audit["whitelist"]), whitelist)
        self.assertTrue(audit["policy_loaded"], audit.get("policy_error"))

    def test_release_whitelist_hash_mismatch_fails_policy_load(self) -> None:
        root, _policy, _whitelist, temp = self._fixture()
        self.addCleanup(temp.cleanup)
        _manifest, whitelist = self._install_release_contract(root)
        whitelist.write_text(f"{BASE}/moskva/\n", encoding="utf-8")
        audit = run_audit(root, base_url=BASE)
        self.assertFalse(audit["policy_loaded"])
        self.assertIn("SHA-256 mismatch", audit["policy_error"])
        ok, breaches = evaluate(audit)
        self.assertFalse(ok)
        self.assertTrue(any("policy_not_loaded" in item for item in breaches))

    def test_missing_release_policy_fails_evaluation_by_default(self) -> None:
        root, _policy, _whitelist, temp = self._fixture()
        self.addCleanup(temp.cleanup)
        audit = run_audit(root, base_url=BASE)
        self.assertFalse(audit["policy_loaded"])
        ok, breaches = evaluate(audit)
        self.assertFalse(ok)
        self.assertTrue(any("policy_not_loaded" in item for item in breaches))

    def test_detects_robots_and_sitemap_inversions(self) -> None:
        root, policy, whitelist, temp = self._fixture()
        self.addCleanup(temp.cleanup)
        (root / "moskva" / "index.html").write_text(
            page_html(canonical=f"{BASE}/moskva/", noindex=True), encoding="utf-8"
        )
        (root / "tula" / "index.html").write_text(
            page_html(canonical=f"{BASE}/tula/", noindex=False), encoding="utf-8"
        )
        (root / "sitemap.xml").write_text(
            f'<?xml version="1.0"?><urlset><url><loc>{BASE}/tula/</loc></url></urlset>',
            encoding="utf-8",
        )
        audit = run_audit(root, base_url=BASE, keep_config=policy, whitelist=whitelist)
        stats = audit["stats"]
        self.assertEqual(stats["unexpected_noindex_open"], 1)
        self.assertEqual(stats["unexpected_index_closed"], 1)
        self.assertEqual(stats["open_missing_sitemap"], 1)
        self.assertEqual(stats["closed_in_sitemap"], 1)

    def test_detects_sitemap_orphan_and_bad_external_url(self) -> None:
        root, policy, whitelist, temp = self._fixture()
        self.addCleanup(temp.cleanup)
        (root / "sitemap.xml").write_text(
            f"""<?xml version="1.0"?><urlset>
<url><loc>{BASE}/moskva/</loc></url>
<url><loc>{BASE}/ghost/</loc></url>
<url><loc>https://evil.example/page/</loc></url>
</urlset>""",
            encoding="utf-8",
        )
        audit = run_audit(root, base_url=BASE, keep_config=policy, whitelist=whitelist)
        self.assertEqual(audit["stats"]["sitemap_orphan_urls"], 1)
        self.assertEqual(audit["stats"]["bad_sitemap_urls"], 1)

    def test_sitemap_index_shard_loc_is_not_counted_as_page_url(self) -> None:
        root, policy, whitelist, temp = self._fixture()
        self.addCleanup(temp.cleanup)
        sitemaps = root / "sitemaps"
        sitemaps.mkdir()
        (root / "sitemap.xml").write_text(
            f"<sitemapindex><sitemap><loc>{BASE}/sitemaps/sitemap-1.xml</loc></sitemap></sitemapindex>",
            encoding="utf-8",
        )
        (sitemaps / "sitemap-1.xml").write_text(
            f"<urlset><url><loc>{BASE}/moskva/</loc></url></urlset>",
            encoding="utf-8",
        )
        audit = run_audit(root, base_url=BASE, keep_config=policy, whitelist=whitelist)
        self.assertEqual(audit["sitemap_urls"], 1)
        self.assertEqual(audit["stats"]["sitemap_orphan_urls"], 0)

    def test_attribute_order_does_not_break_meta_detection(self) -> None:
        root, policy, whitelist, temp = self._fixture()
        self.addCleanup(temp.cleanup)
        audit = run_audit(root, base_url=BASE, keep_config=policy, whitelist=whitelist)
        stats = audit["stats"]
        self.assertEqual(stats["missing_description"], 0)
        self.assertEqual(stats["missing_canonical"], 0)
        self.assertEqual(stats["missing_og_title"], 0)
        self.assertEqual(stats["missing_og_description"], 0)

    def test_detects_broken_internal_link(self) -> None:
        root, policy, whitelist, temp = self._fixture()
        self.addCleanup(temp.cleanup)
        (root / "moskva" / "index.html").write_text(
            page_html(
                canonical=f"{BASE}/moskva/",
                extra_html='<a href="/does-not-exist/">битая ссылка</a>',
            ),
            encoding="utf-8",
        )
        audit = run_audit(root, base_url=BASE, keep_config=policy, whitelist=whitelist)
        self.assertEqual(audit["stats"]["broken_internal_links"], 1)
        self.assertEqual(audit["stats"]["pages_with_broken_internal_links"], 1)

    def test_detects_invalid_jsonld(self) -> None:
        root, policy, whitelist, temp = self._fixture()
        self.addCleanup(temp.cleanup)
        (root / "moskva" / "index.html").write_text(
            page_html(canonical=f"{BASE}/moskva/", jsonld='{"@type":'),
            encoding="utf-8",
        )
        audit = run_audit(root, base_url=BASE, keep_config=policy, whitelist=whitelist)
        self.assertEqual(audit["stats"]["invalid_jsonld_pages"], 1)
        self.assertEqual(audit["stats"]["invalid_jsonld_blocks"], 1)

    def test_detects_duplicate_canonical(self) -> None:
        root, policy, whitelist, temp = self._fixture()
        self.addCleanup(temp.cleanup)
        (root / "tula" / "index.html").write_text(
            page_html(canonical=f"{BASE}/moskva/", noindex=True), encoding="utf-8"
        )
        audit = run_audit(root, base_url=BASE, keep_config=policy, whitelist=whitelist)
        self.assertEqual(audit["duplicates"]["canonical_duplicate_pages"], 2)
        self.assertEqual(audit["duplicates"]["canonical_duplicate_groups"], 1)


if __name__ == "__main__":
    unittest.main()
