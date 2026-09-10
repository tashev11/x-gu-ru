from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from index_policy import normalize_policy_payload, policy_digest
from release_integrity import build_release_metadata, write_release_metadata


MODULE_PATH = Path(__file__).resolve().parents[1] / "server-opt" / "predeploy_check.py"
SPEC = importlib.util.spec_from_file_location("xgu_predeploy_check", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot load predeploy module from {MODULE_PATH}")
predeploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(predeploy)

BASE = "https://x-gu.ru"
REVISION = "a" * 40


def page_html(canonical: str, label: str, *, noindex: bool = False) -> str:
    robots = "noindex, follow" if noindex else "index,follow"
    body = " ".join(["контент"] * 270)
    return f"""<!doctype html>
<html lang="ru"><head>
<title>{label}: проверочная страница безопасного релиза</title>
<meta name="description" content="Проверочное описание страницы {label} достаточной длины для строгого SEO контроля перед безопасной публикацией сайта.">
<meta name="robots" content="{robots}">
<link rel="canonical" href="{canonical}">
<meta property="og:title" content="{label}">
<meta property="og:description" content="Проверка описания {label}">
<script type="application/ld+json">{{"@context":"https://schema.org","@type":"WebPage"}}</script>
</head><body><h1>{label}: контроль релиза</h1><p>{body}</p></body></html>"""


def policy_payload(*, version: int = 1) -> dict:
    if version == 1:
        core = {
            "policy_version": 1,
            "open_cities": ["moskva"],
            "open_services": ["seo-audit-saita"],
        }
    else:
        core = {
            "policy_version": 2,
            "policy_mode": "pairs",
            "open_cities": ["moskva"],
            "open_pairs": ["moskva/seo-audit-saita"],
            "open_services": ["seo-audit-saita"],
        }
    normalized = normalize_policy_payload(core)
    return {
        **core,
        "policy_source": "/opt/p3-app/data/index_policy.json",
        "policy_sha256": policy_digest(normalized),
    }


class PredeployCheckTests(unittest.TestCase):
    def _finalize(self, release: Path) -> None:
        payload = build_release_metadata(
            release,
            tooling_revision=REVISION,
            finalized_at="2026-09-10T18:00:00+00:00",
        )
        write_release_metadata(release, payload)

    def _fixture(self, *, version: int = 1):
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        release = root / "release"
        release.mkdir()
        (release / "index.html").write_text(page_html(f"{BASE}/", "Главная"), encoding="utf-8")
        (release / "moskva").mkdir()
        (release / "moskva" / "index.html").write_text(page_html(f"{BASE}/moskva/", "Москва"), encoding="utf-8")
        (release / "tula").mkdir()
        (release / "tula" / "index.html").write_text(page_html(f"{BASE}/tula/", "Тула", noindex=True), encoding="utf-8")
        (release / "sitemap.xml").write_text(
            f"<?xml version='1.0'?><urlset><url><loc>{BASE}/</loc></url><url><loc>{BASE}/moskva/</loc></url></urlset>",
            encoding="utf-8",
        )

        whitelist = release / predeploy.WHITELIST_FILENAME
        whitelist_text = ""
        whitelist.write_text(whitelist_text, encoding="utf-8")
        keep = release / predeploy.KEEP_FILENAME
        keep.write_text(
            json.dumps(
                {
                    **policy_payload(version=version),
                    "whitelist_source": "/opt/p3-app/data/whitelist.txt",
                    "whitelist_sha256": hashlib.sha256(whitelist_text.encode("utf-8")).hexdigest(),
                }
            ),
            encoding="utf-8",
        )
        self._finalize(release)
        return temp, release, keep, whitelist

    def test_valid_v1_release_and_policy_pass(self) -> None:
        temp, release, keep, whitelist = self._fixture()
        self.addCleanup(temp.cleanup)
        ok, errors, audit = predeploy.run_predeploy(release, keep_config=keep, whitelist=whitelist, base_url=BASE)
        self.assertTrue(ok, errors)
        self.assertEqual(errors, [])
        self.assertIsNotNone(audit)
        self.assertEqual(audit["policy_version"], 1)
        self.assertEqual(audit["policy_mode"], "matrix")
        self.assertEqual(audit["release_metadata"]["tooling_revision"], REVISION)

    def test_valid_v2_pair_policy_passes(self) -> None:
        temp, release, keep, whitelist = self._fixture(version=2)
        self.addCleanup(temp.cleanup)
        ok, errors, audit = predeploy.run_predeploy(release, keep_config=keep, whitelist=whitelist, base_url=BASE)
        self.assertTrue(ok, errors)
        self.assertIsNotNone(audit)
        self.assertEqual(audit["policy_version"], 2)
        self.assertEqual(audit["policy_mode"], "pairs")

    def test_policy_content_change_with_stale_digest_is_rejected(self) -> None:
        temp, release, keep, whitelist = self._fixture(version=2)
        self.addCleanup(temp.cleanup)
        payload = json.loads(keep.read_text(encoding="utf-8"))
        payload["open_pairs"].append("moskva/prodvizhenie-saita")
        payload["open_services"].append("prodvizhenie-saita")
        keep.write_text(json.dumps(payload), encoding="utf-8")
        errors = predeploy.validate_policy_files(keep, whitelist, release_root=release, base_url=BASE)
        self.assertTrue(any("release policy SHA-256 mismatch" in error for error in errors))

    def test_late_release_mutation_is_rejected(self) -> None:
        temp, release, keep, whitelist = self._fixture()
        self.addCleanup(temp.cleanup)
        (release / "index.html").write_text(page_html(f"{BASE}/", "Изменено после финализации"), encoding="utf-8")
        ok, errors, audit = predeploy.run_predeploy(release, keep_config=keep, whitelist=whitelist, base_url=BASE)
        self.assertFalse(ok)
        self.assertIsNone(audit)
        self.assertTrue(any("content SHA-256 mismatch" in error for error in errors))

    def test_keep_config_outside_release_is_rejected(self) -> None:
        temp, release, _keep, whitelist = self._fixture()
        self.addCleanup(temp.cleanup)
        outside = Path(temp.name) / "outside.json"
        outside.write_text(
            json.dumps({
                **policy_payload(),
                "whitelist_source": "reviewed",
                "whitelist_sha256": hashlib.sha256(whitelist.read_bytes()).hexdigest(),
            }),
            encoding="utf-8",
        )
        ok, errors, audit = predeploy.run_predeploy(release, keep_config=outside, whitelist=whitelist, base_url=BASE)
        self.assertFalse(ok)
        self.assertIsNone(audit)
        self.assertTrue(any("release manifest" in error for error in errors))

    def test_whitelist_outside_release_is_rejected(self) -> None:
        temp, release, keep, _whitelist = self._fixture()
        self.addCleanup(temp.cleanup)
        outside = Path(temp.name) / "outside-whitelist.txt"
        outside.write_text("", encoding="utf-8")
        errors = predeploy.validate_policy_files(keep, outside, release_root=release, base_url=BASE)
        self.assertTrue(any("release snapshot" in error for error in errors))

    def test_missing_policy_provenance_fails(self) -> None:
        temp, release, keep, whitelist = self._fixture()
        self.addCleanup(temp.cleanup)
        payload = json.loads(keep.read_text(encoding="utf-8"))
        payload.pop("policy_sha256")
        keep.write_text(json.dumps(payload), encoding="utf-8")
        errors = predeploy.validate_policy_files(keep, whitelist, release_root=release, base_url=BASE)
        self.assertTrue(any("policy_sha256" in error for error in errors))

    def test_invalid_policy_digest_fails(self) -> None:
        temp, release, keep, whitelist = self._fixture()
        self.addCleanup(temp.cleanup)
        payload = json.loads(keep.read_text(encoding="utf-8"))
        payload["policy_sha256"] = "not-a-digest"
        keep.write_text(json.dumps(payload), encoding="utf-8")
        errors = predeploy.validate_policy_files(keep, whitelist, release_root=release, base_url=BASE)
        self.assertTrue(any("valid SHA-256" in error for error in errors))

    def test_whitelist_hash_mismatch_fails(self) -> None:
        temp, release, keep, whitelist = self._fixture()
        self.addCleanup(temp.cleanup)
        whitelist.write_text("https://x-gu.ru/moskva/\n", encoding="utf-8")
        errors = predeploy.validate_policy_files(keep, whitelist, release_root=release, base_url=BASE)
        self.assertTrue(any("whitelist SHA-256 mismatch" in error for error in errors))

    def test_external_whitelist_url_fails(self) -> None:
        temp, release, keep, whitelist = self._fixture()
        self.addCleanup(temp.cleanup)
        text = "https://example.com/moskva/\n"
        whitelist.write_text(text, encoding="utf-8")
        payload = json.loads(keep.read_text(encoding="utf-8"))
        payload["whitelist_sha256"] = hashlib.sha256(text.encode("utf-8")).hexdigest()
        keep.write_text(json.dumps(payload), encoding="utf-8")
        errors = predeploy.validate_policy_files(keep, whitelist, release_root=release, base_url=BASE)
        self.assertTrue(any("not canonical HTTPS" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
