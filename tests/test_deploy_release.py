from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from index_policy import normalize_policy_payload, policy_digest
from release_integrity import build_release_metadata, write_release_metadata


MODULE_PATH = Path(__file__).resolve().parents[1] / "server-opt" / "deploy_release.py"
SPEC = importlib.util.spec_from_file_location("xgu_deploy_release", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot load deploy module from {MODULE_PATH}")
deploy_release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy_release)

REVISION = "a" * 40


class DeployReleaseTests(unittest.TestCase):
    def _finalize(self, release: Path) -> None:
        payload = build_release_metadata(
            release,
            tooling_revision=REVISION,
            finalized_at="2026-09-10T18:00:00+00:00",
        )
        write_release_metadata(release, payload)

    def _release(self, root: Path, name: str, *, policy_version: int = 1) -> Path:
        release = root / name
        release.mkdir()
        (release / "index.html").write_text("<html><head><title>ok</title></head></html>", encoding="utf-8")
        (release / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
        (release / "sitemap.xml").write_text(
            '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>',
            encoding="utf-8",
        )
        whitelist_text = "https://x-gu.ru/moskva/\n"
        whitelist = release / deploy_release.WHITELIST_FILENAME
        whitelist.write_text(whitelist_text, encoding="utf-8")
        whitelist_digest = hashlib.sha256(whitelist_text.encode("utf-8")).hexdigest()

        if policy_version == 1:
            policy_fields = {
                "policy_version": 1,
                "open_cities": ["moskva"],
                "open_services": ["seo-audit-saita"],
            }
        else:
            policy_fields = {
                "policy_version": 2,
                "policy_mode": "pairs",
                "open_cities": ["moskva"],
                "open_pairs": ["moskva/seo-audit-saita"],
                "open_services": ["seo-audit-saita"],
            }
        normalized = normalize_policy_payload(policy_fields)
        (release / deploy_release.KEEP_FILENAME).write_text(
            json.dumps({
                **policy_fields,
                "policy_source": "/reviewed/index_policy.json",
                "policy_sha256": policy_digest(normalized),
                "whitelist_source": "/reviewed/whitelist.txt",
                "whitelist_sha256": whitelist_digest,
            }),
            encoding="utf-8",
        )
        self._finalize(release)
        return release

    def test_validate_release_requires_finalized_core_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            release = Path(temp) / "broken"
            release.mkdir()
            errors = deploy_release.validate_release(release)
            self.assertGreaterEqual(len(errors), 6)
            self.assertTrue(any(deploy_release.KEEP_FILENAME in error for error in errors))
            self.assertTrue(any(deploy_release.WHITELIST_FILENAME in error for error in errors))
            self.assertTrue(any(deploy_release.RELEASE_METADATA_FILENAME in error for error in errors))

    def test_valid_v2_policy_passes_structural_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            release = self._release(Path(temp), "candidate", policy_version=2)
            errors = deploy_release.validate_release(release)
            self.assertEqual(errors, [])

    def test_v2_pair_under_closed_city_is_rejected_structurally(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            release = self._release(Path(temp), "candidate", policy_version=2)
            keep = release / deploy_release.KEEP_FILENAME
            payload = json.loads(keep.read_text(encoding="utf-8"))
            payload["open_cities"] = ["tver"]
            keep.write_text(json.dumps(payload), encoding="utf-8")
            errors = deploy_release.validate_release(release)
            self.assertTrue(any("policy is invalid" in error for error in errors))

    def test_stale_policy_digest_is_rejected_structurally(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            release = self._release(Path(temp), "candidate", policy_version=2)
            keep = release / deploy_release.KEEP_FILENAME
            payload = json.loads(keep.read_text(encoding="utf-8"))
            payload["open_pairs"].append("moskva/prodvizhenie-saita")
            payload["open_services"].append("prodvizhenie-saita")
            keep.write_text(json.dumps(payload), encoding="utf-8")
            errors = deploy_release.validate_release(release)
            self.assertTrue(any("policy SHA-256 mismatch" in error for error in errors))

    def test_invalid_keep_manifest_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            release = self._release(Path(temp), "candidate")
            (release / deploy_release.KEEP_FILENAME).write_text(
                json.dumps({
                    "policy_version": 1,
                    "open_cities": ["moskva"],
                    "open_services": ["seo-audit-saita"],
                    "policy_source": "reviewed",
                    "policy_sha256": "bad",
                    "whitelist_source": "reviewed",
                    "whitelist_sha256": "bad",
                }),
                encoding="utf-8",
            )
            errors = deploy_release.validate_release(release)
            self.assertTrue(any("invalid policy_sha256" in error for error in errors))
            self.assertTrue(any("invalid whitelist_sha256" in error for error in errors))

    def test_atomic_switch_returns_previous_target(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            releases = root / "releases"
            releases.mkdir()
            old = self._release(releases, "old")
            new = self._release(releases, "new")
            current = root / "current"
            current.symlink_to(old)
            previous = deploy_release.switch_release(current, new, releases_root=releases)
            self.assertEqual(previous, old.resolve())
            self.assertTrue(current.is_symlink())
            self.assertEqual(current.resolve(), new.resolve())

    def test_refuses_real_current_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            current = Path(temp) / "current"
            current.mkdir()
            with self.assertRaises(RuntimeError):
                deploy_release._current_target(current)

    def test_refuses_release_outside_releases_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            releases = root / "releases"
            releases.mkdir()
            outside = self._release(root, "outside")
            errors = deploy_release.validate_release(outside, releases_root=releases)
            self.assertTrue(any("direct child" in error for error in errors))
            with self.assertRaises(RuntimeError):
                deploy_release.switch_release(root / "current", outside, releases_root=releases)

    def test_sitemap_index_requires_existing_local_shards(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            releases = root / "releases"
            releases.mkdir()
            release = self._release(releases, "candidate")
            (release / "sitemap.xml").write_text(
                """<?xml version="1.0"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://x-gu.ru/sitemaps/sitemap-1.xml</loc></sitemap>
</sitemapindex>""",
                encoding="utf-8",
            )
            errors = deploy_release.validate_release(release, releases_root=releases)
            self.assertTrue(any("referenced sitemap shard missing" in error for error in errors))

            shard = release / "sitemaps" / "sitemap-1.xml"
            shard.parent.mkdir()
            shard.write_text(
                '<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>',
                encoding="utf-8",
            )
            errors = deploy_release.validate_release(release, releases_root=releases)
            self.assertEqual(errors, [])

    def test_sitemap_index_rejects_noncanonical_host(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            releases = root / "releases"
            releases.mkdir()
            release = self._release(releases, "candidate")
            (release / "sitemap.xml").write_text(
                """<?xml version="1.0"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://www.x-gu.ru/sitemaps/sitemap-1.xml</loc></sitemap>
</sitemapindex>""",
                encoding="utf-8",
            )
            errors = deploy_release.validate_release(release, releases_root=releases)
            self.assertTrue(any("not canonical HTTPS" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
