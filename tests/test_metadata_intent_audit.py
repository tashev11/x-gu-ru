from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "server-opt" / "metadata_intent_audit.py"
SPEC = importlib.util.spec_from_file_location("xgu_metadata_intent_audit", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot load metadata intent audit from {MODULE_PATH}")
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)

BASE = "https://x-gu.ru"


def html_page(url: str, *, title: str, description: str, h1: str) -> str:
    return f"""<!doctype html><html lang="ru"><head>
<title>{title}</title>
<meta name="description" content="{description}">
<link rel="canonical" href="{url}">
</head><body><h1>{h1}</h1><p>{'полезный контент ' * 320}</p></body></html>"""


class MetadataIntentAuditTests(unittest.TestCase):
    def _release(self) -> tuple[tempfile.TemporaryDirectory, Path]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        (root / "index.html").write_text(
            html_page(f"{BASE}/", title="Главная x-gu.ru", description="Главная страница", h1="Главная"),
            encoding="utf-8",
        )
        pages = {
            ("moskva", "seo-audit-saita"): (
                "SEO аудит сайта в Москве | СЕО ГУРУ",
                "SEO аудит сайта в Москве. Проверяем технические ошибки, структуру и индексирование сайта.",
                "SEO аудит сайта в Москве",
            ),
            ("tver", "seo-audit-saita"): (
                "SEO аудит сайта в Твери | СЕО ГУРУ",
                "SEO аудит сайта в Твери. Проверяем технические ошибки, структуру и индексирование сайта.",
                "SEO аудит сайта в Твери",
            ),
            ("kazan", "seo-audit-saita"): (
                "SEO аудит сайта в Казани | СЕО ГУРУ",
                "SEO аудит сайта в Казани. Проверяем технические ошибки, структуру и индексирование сайта.",
                "SEO аудит сайта в Казани",
            ),
            ("moskva", "seo-prodvizhenie-saita"): (
                "SEO продвижение сайта в Москве | СЕО ГУРУ",
                "SEO продвижение сайта в Москве. Работаем со структурой, контентом и индексированием сайта.",
                "SEO продвижение сайта в Москве",
            ),
        }
        for (city, service), (title, desc, h1) in pages.items():
            path = root / city / service
            path.mkdir(parents=True)
            url = f"{BASE}/{city}/{service}/"
            (path / "index.html").write_text(
                html_page(url, title=title, description=desc, h1=h1),
                encoding="utf-8",
            )

        whitelist = root / ".xgu-whitelist.txt"
        whitelist.write_text("", encoding="utf-8")
        (root / ".xgu-index-keep.json").write_text(
            json.dumps(
                {
                    "policy_version": 1,
                    "open_cities": ["moskva", "tver", "kazan"],
                    "open_services": ["seo-audit-saita", "seo-prodvizhenie-saita"],
                    "policy_source": "test",
                    "policy_sha256": "a" * 64,
                    "whitelist_source": "test",
                    "whitelist_sha256": hashlib.sha256(b"").hexdigest(),
                }
            ),
            encoding="utf-8",
        )
        return temp, root

    def test_same_service_city_variants_are_detected_as_template_risk(self) -> None:
        temp, root = self._release()
        self.addCleanup(temp.cleanup)
        audit = mod.run_audit(
            root,
            same_service_threshold=0.75,
            cross_service_threshold=0.95,
            service_template_ratio=0.60,
        )
        row = next(item for item in audit["service_stats"] if item["service"] == "seo-audit-saita")
        self.assertEqual(row["pages"], 3)
        self.assertTrue(row["template_risk"])
        self.assertGreaterEqual(row["high_similarity_pairs"], 2)
        self.assertGreaterEqual(audit["services_with_template_risk"], 1)

    def test_cross_service_same_city_similarity_can_be_reported(self) -> None:
        temp, root = self._release()
        self.addCleanup(temp.cleanup)
        audit = mod.run_audit(
            root,
            same_service_threshold=0.99,
            cross_service_threshold=0.55,
        )
        pairs = audit["cross_service_examples"]
        self.assertTrue(
            any(
                item.get("city") == "moskva"
                and {item["service_left"], item["service_right"]}
                == {"seo-audit-saita", "seo-prodvizhenie-saita"}
                for item in pairs
            )
        )

    def test_metadata_similarity_exposes_field_level_scores(self) -> None:
        left = mod.MetaRecord(
            "u1", "moskva", "seo-audit", "SEO аудит в Москве", "SEO аудит в Москве для бизнеса", "SEO аудит в Москве"
        )
        right = mod.MetaRecord(
            "u2", "tver", "seo-audit", "SEO аудит в Твери", "SEO аудит в Твери для бизнеса", "SEO аудит в Твери"
        )
        scores = mod.metadata_similarity(left, right)
        self.assertGreater(scores["combined"], 0.70)
        self.assertIn("title", scores)
        self.assertIn("h1", scores)
        self.assertIn("description", scores)

    def test_invalid_threshold_is_rejected(self) -> None:
        temp, root = self._release()
        self.addCleanup(temp.cleanup)
        with self.assertRaises(ValueError):
            mod.run_audit(root, same_service_threshold=1.1)


if __name__ == "__main__":
    unittest.main()
