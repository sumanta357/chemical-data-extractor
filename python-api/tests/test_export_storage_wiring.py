"""Durable export storage, as wired into the running app.

test_gcs_storage.py covers the storage module on its own. This covers the two
places app.py has to get right for an instance that is *already deployed*:
the SQLite migration, because every live deployment already has a `searches`
table without the storage column, and the persistence round trip, because an
archive that is uploaded but never recorded is a link nobody can reach.
"""

import asyncio
import sqlite3
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as engine_app  # noqa: E402
import gcs_storage  # noqa: E402


# The schema as it existed before export storage was added.
LEGACY_SCHEMA = """
CREATE TABLE searches (
    search_id TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    query_type TEXT DEFAULT 'auto',
    hops INTEGER DEFAULT 1,
    status TEXT DEFAULT 'queued',
    progress TEXT DEFAULT '',
    log TEXT DEFAULT '[]',
    export_files TEXT DEFAULT '[]',
    export_dir TEXT DEFAULT '',
    created_at TEXT DEFAULT '',
    elapsed_seconds REAL,
    error TEXT
)
"""


class SchemaMigration(unittest.TestCase):
    """An existing deployment must survive the upgrade without losing rows."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db = Path(self.tmp.name) / "searches.db"

    def tearDown(self):
        self.tmp.cleanup()

    def _columns(self) -> set:
        with sqlite3.connect(str(self.db)) as conn:
            return {r[1] for r in conn.execute("PRAGMA table_info(searches)")}

    def test_legacy_database_gains_the_storage_column(self):
        with sqlite3.connect(str(self.db)) as conn:
            conn.execute(LEGACY_SCHEMA)

        with mock.patch.object(engine_app, "SEARCH_DB", self.db):
            engine_app._init_db()

        self.assertIn("storage", self._columns())

    def test_migration_keeps_existing_rows(self):
        with sqlite3.connect(str(self.db)) as conn:
            conn.execute(LEGACY_SCHEMA)
            conn.execute(
                "INSERT INTO searches (search_id, query, status) VALUES (?, ?, ?)",
                ("old1", "aspirin", "completed"),
            )

        with mock.patch.object(engine_app, "SEARCH_DB", self.db):
            engine_app._init_db()

        with sqlite3.connect(str(self.db)) as conn:
            rows = conn.execute("SELECT search_id, query FROM searches").fetchall()
        self.assertEqual(rows, [("old1", "aspirin")])

    def test_migration_is_idempotent(self):
        # _init_db runs on every boot, and the CREATE has IF NOT EXISTS while
        # the ALTER does not, so a second boot must not raise.
        with mock.patch.object(engine_app, "SEARCH_DB", self.db):
            engine_app._init_db()
            engine_app._init_db()
        self.assertIn("storage", self._columns())

    def test_fresh_database_has_the_column(self):
        with mock.patch.object(engine_app, "SEARCH_DB", self.db):
            engine_app._init_db()
        self.assertIn("storage", self._columns())


class ArchivePersistence(unittest.TestCase):
    """The uploaded object must come back with the search, or the link is lost."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.db = Path(self.tmp.name) / "searches.db"
        with mock.patch.object(engine_app, "SEARCH_DB", self.db):
            engine_app._init_db()

        self.archive = {
            "provider": "gcs",
            "bucket": "my-bucket",
            "object": "exports/7/abc123/abc123_EGFR_20260101-000000_ab12.zip",
            "filename": "abc123_EGFR_20260101-000000_ab12.zip",
            "size_bytes": 1234,
            "size_display": "1.2 KB",
            "uploaded_at": "2026-01-01T00:00:00+00:00",
        }
        self.state = {
            "search_id": "abc123",
            "query": "EGFR",
            "query_type": "auto",
            "hops": 2,
            "status": "completed",
            "progress": "done",
            "log": ["line"],
            "export_files": [],
            "export_dir": "/tmp/nowhere",
            "created_at": "2026-01-01T00:00:00",
            "elapsed_seconds": 1.5,
            "error": None,
            "owner_user_id": 7,
            "storage": self.archive,
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_storage_survives_a_save(self):
        with mock.patch.object(engine_app, "SEARCH_DB", self.db):
            engine_app._save_search(self.state)
        with sqlite3.connect(str(self.db)) as conn:
            raw = conn.execute(
                "SELECT storage FROM searches WHERE search_id = ?", ("abc123",)
            ).fetchone()[0]
        self.assertIn("exports/7/abc123", raw)

    def test_search_without_storage_saves_an_empty_object_not_null(self):
        # A NULL here would break the startup loader's json.loads.
        state = dict(self.state)
        state.pop("storage")
        with mock.patch.object(engine_app, "SEARCH_DB", self.db):
            engine_app._save_search(state)
        with sqlite3.connect(str(self.db)) as conn:
            raw = conn.execute(
                "SELECT storage FROM searches WHERE search_id = ?", ("abc123",)
            ).fetchone()[0]
        self.assertEqual(raw, "{}")

    def test_resaving_does_not_drop_the_archive(self):
        with mock.patch.object(engine_app, "SEARCH_DB", self.db):
            engine_app._save_search(self.state)
            # The completion path saves again after setting progress.
            engine_app._save_search({**self.state, "progress": "✅ done"})
        with sqlite3.connect(str(self.db)) as conn:
            raw = conn.execute(
                "SELECT storage FROM searches WHERE search_id = ?", ("abc123",)
            ).fetchone()[0]
        self.assertIn("exports/7/abc123", raw)


class SearchStatusContract(unittest.TestCase):
    """The frontend only shows the archive because the API advertises it."""

    def test_status_model_carries_the_archive(self):
        status = engine_app.SearchStatus(
            search_id="abc123", query="EGFR", query_type="auto", hops=2,
            status="completed", progress=None, log=[], export_files=[],
            created_at="2026-01-01T00:00:00", elapsed_seconds=1.0, error=None,
            storage={"provider": "gcs", "object": "exports/7/abc123/a.zip"},
        )
        self.assertEqual(status.storage["object"], "exports/7/abc123/a.zip")

    def test_status_model_tolerates_a_search_with_no_archive(self):
        status = engine_app.SearchStatus(
            search_id="abc123", query="EGFR", query_type="auto", hops=2,
            status="completed", progress=None, log=[], export_files=[],
            created_at="2026-01-01T00:00:00", elapsed_seconds=1.0, error=None,
        )
        self.assertIsNone(status.storage)


class HealthReporting(unittest.TestCase):
    """Health has to say whether archiving is on, so support can tell."""

    def test_health_reports_storage_not_configured(self):
        with mock.patch.object(gcs_storage, "is_configured", return_value=False), \
                mock.patch.object(gcs_storage, "bucket_name", return_value=""):
            report = asyncio.run(engine_app.health())
        self.assertFalse(report["export_storage"]["configured"])
        self.assertIsNone(report["export_storage"]["bucket"])

    def test_health_reports_the_bucket_when_configured(self):
        with mock.patch.object(gcs_storage, "is_configured", return_value=True), \
                mock.patch.object(gcs_storage, "bucket_name", return_value="my-bucket"):
            report = asyncio.run(engine_app.health())
        self.assertTrue(report["export_storage"]["configured"])
        self.assertEqual(report["export_storage"]["bucket"], "my-bucket")

    def test_health_is_not_broken_when_the_storage_layer_is_missing(self):
        # The import is guarded, so _gcs can be None on a bad deploy.
        with mock.patch.object(engine_app, "_gcs", None):
            report = asyncio.run(engine_app.health())
        self.assertFalse(report["export_storage"]["configured"])

    def test_no_storage_on_a_completed_search_leaves_the_field_absent(self):
        state = {"search_id": "abc123", "status": "completed"}
        cleaned = engine_app._clean_status(state)
        self.assertNotIn("storage", cleaned)


if __name__ == "__main__":
    unittest.main()
