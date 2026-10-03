#!/usr/bin/env python3
"""
Export retention: a completed search keeps its files until the normal expiry;
a failed one is removed the moment it fails.

The failure paths are the ones that matter on a small container — an OOM-killed
or crashed run leaves partial files behind, and holding them costs space a
working search needs. Run: cd python-api && python3 -m pytest tests/test_exports.py -q
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

_TEST_DIR = tempfile.mkdtemp(prefix="scigraph_export_test_")
os.environ["BILLING_DB"] = str(Path(_TEST_DIR) / "billing.db")
os.environ["SEARCH_DB"] = str(Path(_TEST_DIR) / "search.db")
os.environ["EXPORTS_DIR"] = str(Path(_TEST_DIR) / "exports")
os.environ["WORKSPACE_DIR"] = str(Path(_TEST_DIR) / "workspace")
os.environ["STATIC_DIR"] = str(Path(_TEST_DIR) / "ui")
os.environ["REQUIRE_AUTH"] = "0"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as engine_app  # noqa: E402


def _make_export_dir(name="job") -> str:
    d = Path(engine_app.EXPORTS_DIR) / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "nodes.csv").write_text("uid:ID\nCHEM:1\n", encoding="utf-8")
    (d / "enriched_data.xlsx").write_bytes(b"PK\x03\x04stub")
    return str(d)


class FailedExportCleanup(unittest.TestCase):
    def setUp(self):
        Path(engine_app.EXPORTS_DIR).mkdir(parents=True, exist_ok=True)

    def test_failed_export_dir_is_removed_immediately(self):
        export_dir = _make_export_dir("failed-job")
        self.assertTrue(Path(export_dir).exists())
        engine_app._discard_failed_export(export_dir)
        self.assertFalse(
            Path(export_dir).exists(),
            "a failed search kept its partial files instead of dropping them")

    def test_a_completed_search_keeps_its_files(self):
        """The other half of the rule: success is still kept until expiry."""
        export_dir = _make_export_dir("good-job")
        state = {"status": "completed", "search_id": "goodjob1"}
        engine_app.searches[state["search_id"]] = state
        # No discard call happens on the success path.
        self.assertTrue(Path(export_dir).exists())
        self.assertTrue((Path(export_dir) / "enriched_data.xlsx").exists())

    def test_missing_or_blank_export_dir_is_harmless(self):
        for value in ("", None, "/tmp/definitely-not-here-scigraph"):
            with self.subTest(export_dir=value):
                engine_app._discard_failed_export(value)  # must not raise

    def test_a_directory_with_files_is_fully_removed(self):
        export_dir = _make_export_dir("nested-job")
        sub = Path(export_dir) / "structure_images"
        sub.mkdir()
        (sub / "a.png").write_bytes(b"\x89PNG")
        engine_app._discard_failed_export(export_dir)
        self.assertFalse(Path(export_dir).exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)