#!/usr/bin/env python3
"""
SEARCH_ENRICH_IMAGES is an operational switch: it is set in the Render
environment, not in the repository, so a typo or a rename turns the memory
mitigation into a silent no-op and nobody finds out until a search is
OOM-killed on the 512MB free tier. The switch is therefore pinned from both
ends — the argv it produces, and the environment variable name it reads.

Run:  cd python-api && python3 -m pytest tests/test_enrich_images.py -q
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

_TEST_DIR = tempfile.mkdtemp(prefix="scigraph_enrich_images_test_")
os.environ["BILLING_DB"] = str(Path(_TEST_DIR) / "billing.db")
os.environ["SEARCH_DB"] = str(Path(_TEST_DIR) / "search.db")
os.environ["EXPORTS_DIR"] = str(Path(_TEST_DIR) / "exports")
os.environ["WORKSPACE_DIR"] = str(Path(_TEST_DIR) / "workspace")
os.environ["STATIC_DIR"] = str(Path(_TEST_DIR) / "ui")

_MODULE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_MODULE_DIR))

import app as engine_app  # noqa: E402


class EnrichCommand(unittest.TestCase):
    """The switch has to reach the subprocess argv, because that is the only
    place it can have any effect."""

    def setUp(self):
        self._original = engine_app.ENRICH_IMAGES
        self.addCleanup(
            lambda: setattr(engine_app, "ENRICH_IMAGES", self._original))

    def test_images_are_downloaded_by_default(self):
        engine_app.ENRICH_IMAGES = True
        cmd = engine_app._enrich_command("/tmp/exports/job1")
        self.assertNotIn(
            "--skip-images", cmd,
            "images were skipped even though SEARCH_ENRICH_IMAGES was not set to 0")

    def test_switching_images_off_reaches_the_engine(self):
        """The whole point of SEARCH_ENRICH_IMAGES=0 on Render."""
        engine_app.ENRICH_IMAGES = False
        cmd = engine_app._enrich_command("/tmp/exports/job1")
        self.assertIn(
            "--skip-images", cmd,
            "SEARCH_ENRICH_IMAGES=0 produced no --skip-images, so the memory "
            "saving silently does nothing")

    def test_off_still_produces_the_enriched_workbook(self):
        """The flag must only drop thumbnails — the workbook is the product."""
        engine_app.ENRICH_IMAGES = False
        cmd = engine_app._enrich_command("/tmp/exports/job1")
        self.assertTrue(cmd[1].endswith("enrich_exports.py"), cmd)
        self.assertEqual(cmd[cmd.index("--export-dir") + 1], "/tmp/exports/job1")

    def test_the_flag_is_the_last_argument(self):
        """Argparse must still parse it if more options are added later."""
        engine_app.ENRICH_IMAGES = False
        cmd = engine_app._enrich_command("/tmp/exports/job1")
        self.assertEqual(cmd[-1], "--skip-images")

    def test_the_engine_accepts_the_flag_we_pass(self):
        """The server and the engine could disagree about the spelling. This
        runs the real argparse, so a rename on either side fails here."""
        engine_app.ENRICH_IMAGES = False
        cmd = engine_app._enrich_command("/tmp/exports/job1")
        import subprocess
        result = subprocess.run(
            [sys.executable, cmd[1], "--help"],
            capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--skip-images", result.stdout,
                      "enrich_exports.py no longer accepts --skip-images")


class EnvironmentVariableName(unittest.TestCase):
    """The value lives in Render, so the name in the source is the contract."""

    def test_the_switch_reads_the_documented_variable_name(self):
        source = (_MODULE_DIR / "app.py").read_text(encoding="utf-8")
        self.assertIn(
            'os.environ.get("SEARCH_ENRICH_IMAGES"', source,
            "app.py no longer reads SEARCH_ENRICH_IMAGES; a variable of that "
            "name is now set on Render but silently ignored")

    def test_the_documented_name_matches_the_source(self):
        docs = (_MODULE_DIR.parent / "docs" / "BILLING.md")
        if not docs.exists():
            self.skipTest("no BILLING.md in this checkout")
        self.assertIn("SEARCH_ENRICH_IMAGES", docs.read_text(encoding="utf-8"))


class SettingParsing(unittest.TestCase):
    """What the operator actually types has to land in the right state."""

    def _parse(self, raw: str) -> bool:
        """The production parser, not a copy of it — a copy would keep
        passing after the real one changed, which is how a stale test hides a
        regression."""
        return engine_app._parse_flag(raw)

    def test_zero_and_its_spellings_all_turn_images_off(self):
        for raw in ("0", "false", "FALSE", "no", "off", " Off "):
            with self.subTest(value=raw):
                self.assertFalse(self._parse(raw),
                                 f"SEARCH_ENRICH_IMAGES={raw!r} did not turn images off")

    def test_quoted_values_still_turn_images_off(self):
        """Render's environment fields take whatever is pasted in, and a
        quoted value is the obvious paste accident. It has to mean the same
        thing as the bare value, or the switch silently does nothing — which is
        exactly the failure this whole switch exists to prevent."""
        for raw in ('"0"', "'0'", ' "false" ', "'OFF'", '""0""'):
            with self.subTest(value=raw):
                self.assertFalse(
                    self._parse(raw),
                    f"SEARCH_ENRICH_IMAGES={raw!r} was not read as off")

    def test_anything_else_keeps_images_on(self):
        for raw in ("1", "true", "yes", "on", ""):
            with self.subTest(value=raw):
                self.assertTrue(self._parse(raw))


class HealthReportsEffectiveSettings(unittest.TestCase):
    """The values live in a dashboard, so the running container has to be able
    to say what it actually resolved them to."""

    def _health(self) -> dict:
        """Call the real endpoint, so these tests cover the shipped response."""
        import asyncio
        return asyncio.run(engine_app.health())

    def test_health_exposes_the_image_switch(self):
        result = self._health()
        self.assertIn("enrich_images", result)
        self.assertIsInstance(result["enrich_images"], bool)

    def test_health_exposes_the_watchdog_limits(self):
        """Lets a deployment be confirmed without waiting for a search to fail
        the old way."""
        watchdog = self._health()["watchdog"]
        self.assertEqual(watchdog["stall_timeout"], engine_app.STALL_TIMEOUT)
        self.assertEqual(
            watchdog["max_search_seconds"], engine_app.MAX_SEARCH_SECONDS)

    def test_health_reports_the_switch_as_the_container_resolved_it(self):
        self._original = engine_app.ENRICH_IMAGES
        self.addCleanup(
            lambda: setattr(engine_app, "ENRICH_IMAGES", self._original))
        engine_app.ENRICH_IMAGES = False
        self.assertFalse(self._health()["enrich_images"])


if __name__ == "__main__":
    unittest.main(verbosity=2)