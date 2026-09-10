from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from release_integrity import (
    RELEASE_METADATA_FILENAME,
    build_release_metadata,
    normalize_revision,
    verify_release_metadata,
    write_release_metadata,
)


REVISION = "a" * 40


class ReleaseIntegrityTests(unittest.TestCase):
    def _release(self, root: Path) -> Path:
        release = root / "release"
        release.mkdir()
        (release / "index.html").write_text("<html>one</html>", encoding="utf-8")
        (release / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
        nested = release / "city"
        nested.mkdir()
        (nested / "index.html").write_text("<html>city</html>", encoding="utf-8")
        return release

    def test_revision_requires_full_git_sha(self) -> None:
        self.assertEqual(normalize_revision(REVISION.upper()), REVISION)
        for invalid in ("", "abc", "g" * 40, "a" * 39, "a" * 41):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    normalize_revision(invalid)

    def test_finalized_release_verifies(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            release = self._release(Path(temp))
            payload = build_release_metadata(
                release,
                tooling_revision=REVISION,
                finalized_at="2026-09-10T18:00:00+00:00",
                source_release="r1",
            )
            write_release_metadata(release, payload)

            verified, errors = verify_release_metadata(release)

            self.assertEqual(errors, [])
            self.assertIsNotNone(verified)
            self.assertEqual(verified["tooling_revision"], REVISION)
            self.assertEqual(verified["source_release"], "r1")
            self.assertEqual(verified["file_count"], 3)

    def test_metadata_file_does_not_hash_itself(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            release = self._release(Path(temp))
            payload = build_release_metadata(
                release,
                tooling_revision=REVISION,
                finalized_at="2026-09-10T18:00:00+00:00",
            )
            write_release_metadata(release, payload)
            first = (release / RELEASE_METADATA_FILENAME).read_text(encoding="utf-8")

            payload2 = build_release_metadata(
                release,
                tooling_revision=REVISION,
                finalized_at="2026-09-10T18:01:00+00:00",
            )

            self.assertEqual(payload["content_sha256"], payload2["content_sha256"])
            self.assertEqual(payload["file_count"], payload2["file_count"])
            self.assertTrue(first)

    def test_file_change_after_finalization_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            release = self._release(Path(temp))
            payload = build_release_metadata(
                release,
                tooling_revision=REVISION,
                finalized_at="2026-09-10T18:00:00+00:00",
            )
            write_release_metadata(release, payload)
            (release / "index.html").write_text("<html>changed</html>", encoding="utf-8")

            _verified, errors = verify_release_metadata(release)

            self.assertTrue(any("content SHA-256 mismatch" in error for error in errors))

    def test_new_file_after_finalization_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            release = self._release(Path(temp))
            payload = build_release_metadata(
                release,
                tooling_revision=REVISION,
                finalized_at="2026-09-10T18:00:00+00:00",
            )
            write_release_metadata(release, payload)
            (release / "unexpected.txt").write_text("late mutation", encoding="utf-8")

            _verified, errors = verify_release_metadata(release)

            self.assertTrue(any("content SHA-256 mismatch" in error for error in errors))
            self.assertTrue(any("file_count mismatch" in error for error in errors))

    def test_symlink_inside_release_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            release = self._release(base)
            outside = base / "outside.txt"
            outside.write_text("external", encoding="utf-8")
            (release / "external-link").symlink_to(outside)

            with self.assertRaises(ValueError):
                build_release_metadata(
                    release,
                    tooling_revision=REVISION,
                    finalized_at="2026-09-10T18:00:00+00:00",
                )


if __name__ == "__main__":
    unittest.main()
