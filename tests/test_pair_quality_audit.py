from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "server-opt" / "pair_quality_audit.py"
SPEC = importlib.util.spec_from_file_location("xgu_pair_quality_audit", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot load pair quality audit from {MODULE_PATH}")
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)

BASE = "https://x-gu.ru"


def html(url: str, label: str, *, words: int = 320) -> str:
    body = " ".join([f"полезный-{label}"] * words)
    return f"""<!doctype html><html lang="ru"><head>
<title>{label}: SEO услуга в городе</title>
<meta name="description" content="Подробное описание услуги {label} для теста качества программных SEO страниц.">
<link rel="canonical" href="{url}">
<script type="application/ld+json">{{"@context":"https://schema.org","@type":"WebPage"}}</script>
</head><body><h1>{label}</h1><p>{body}</p></body></html>"""


class PairQualityAuditTests(unittest.TestCase):
    def _write_page(self, root: Path, city: str, service: str, content: str) -> None:
        path = root / city / service
        path.mkdir(parents=True)
        (path / "index.html").write_text(content, encoding="utf-8")

    def test_clean_pair_is_recommended_clean(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            url = f"{BASE}/moskva/seo-audit-saita/"
            self._write_page(root, "moskva", "seo-audit-saita", html(url, "Москва аудит"))
            audit = mod.audit_pairs(root, [{"url": url}], min_words=100)
            self.assertEqual(audit["pairs_audited"], 1)
            self.assertEqual(audit["clean_pairs"], 1)
            self.assertEqual(audit["pairs"][0]["quality_state"], "clean")
            self.assertEqual(audit["pairs"][0]["flags"], [])

    def test_missing_and_thin_pages_need_improvement(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            thin_url = f"{BASE}/moskva/thin-service/"
            missing_url = f"{BASE}/tver/missing-service/"
            self._write_page(root, "moskva", "thin-service", html(thin_url, "Тонкая", words=10))
            audit = mod.audit_pairs(root, [{"url": thin_url}, {"url": missing_url}], min_words=100)
            by_url = {item["url"]: item for item in audit["pairs"]}
            self.assertIn("thin_content", by_url[thin_url]["flags"])
            self.assertEqual(by_url[thin_url]["quality_state"], "improve_before_index")
            self.assertIn("missing_page", by_url[missing_url]["flags"])

    def test_canonical_mismatch_is_hard_quality_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            url = f"{BASE}/moskva/service-a/"
            self._write_page(root, "moskva", "service-a", html(f"{BASE}/moskva/other/", "Mismatch"))
            audit = mod.audit_pairs(root, [{"url": url}], min_words=100)
            self.assertIn("canonical_mismatch", audit["pairs"][0]["flags"])
            self.assertEqual(audit["pairs"][0]["quality_state"], "improve_before_index")

    def test_exact_duplicate_pairs_are_flagged(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first = f"{BASE}/moskva/service-a/"
            second = f"{BASE}/tver/service-a/"
            shared = html(first, "Одинаковый")
            self._write_page(root, "moskva", "service-a", shared)
            # Keep visible body identical but canonical correct for the second URL.
            second_html = shared.replace(f'href="{first}"', f'href="{second}"')
            self._write_page(root, "tver", "service-a", second_html)
            audit = mod.audit_pairs(root, [{"url": first}, {"url": second}], min_words=100)
            self.assertEqual(len(audit["exact_duplicate_groups"]), 1)
            for item in audit["pairs"]:
                self.assertIn("exact_duplicate", item["flags"])
                self.assertEqual(item["quality_state"], "improve_before_index")

    def test_non_pair_evidence_url_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            audit = mod.audit_pairs(Path(temp), [{"url": f"{BASE}/moskva/"}], min_words=100)
            self.assertEqual(audit["pairs_audited"], 0)


if __name__ == "__main__":
    unittest.main()
