from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "server-opt" / "seo_snapshot.py"
SPEC = importlib.util.spec_from_file_location("xgu_seo_snapshot", MODULE_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"Cannot load SEO snapshot from {MODULE_PATH}")
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


class SeoSnapshotTests(unittest.TestCase):
    def test_pipeline_contains_only_report_write_tools(self) -> None:
        dangerous = {
            "shrink_index.py",
            "purge_closed_pages.py",
            "deploy_release.py",
            "bootstrap_release_layout.py",
            "rerender_open_hubs.py",
            "rerender_hubs_home.py",
            "seo_rebuild_broken.py",
        }
        self.assertTrue(mod.REPORT_WRITE_ONLY_SCRIPTS.isdisjoint(dangerous))

    def test_steps_build_complete_action_queue_pipeline(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "current"
            out = Path(temp) / "snapshot"
            root.mkdir()
            out.mkdir()
            steps = mod.build_steps(
                python="python-test",
                root=root,
                out_dir=out,
                days=90,
                max_input_age_days=14,
            )
        names = [name for name, _command in steps]
        self.assertEqual(names[0], "search evidence")
        self.assertIn("pair quality", names)
        self.assertIn("index coverage cohorts", names)
        self.assertIn("GSC cannibalization", names)
        self.assertEqual(names[-1], "unified action queue")
        self.assertEqual(len(names), 11)

    def test_no_snapshot_command_targets_live_mutation_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "current"
            out = Path(temp) / "snapshot"
            root.mkdir()
            out.mkdir()
            steps = mod.build_steps(
                python="python-test",
                root=root,
                out_dir=out,
                days=28,
                max_input_age_days=7,
            )
        command_text = "\n".join(" ".join(command) for _name, command in steps)
        for forbidden in (
            "shrink_index.py",
            "purge_closed_pages.py",
            "deploy_release.py",
            "bootstrap_release_layout.py",
            "rerender_open_hubs.py",
            "rerender_hubs_home.py",
        ):
            self.assertNotIn(forbidden, command_text)

    def test_report_outputs_are_scoped_to_snapshot_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "current"
            out = Path(temp) / "snapshot"
            root.mkdir()
            out.mkdir()
            steps = mod.build_steps(
                python="python-test",
                root=root,
                out_dir=out,
                days=90,
                max_input_age_days=14,
            )
            command_text = "\n".join(" ".join(command) for _name, command in steps)
            self.assertIn(str(out / "search_evidence.json"), command_text)
            self.assertIn(str(out / "seo_action_queue.json"), command_text)
            self.assertNotIn("/var/www/x-gu.ru/releases/", command_text)


if __name__ == "__main__":
    unittest.main()
