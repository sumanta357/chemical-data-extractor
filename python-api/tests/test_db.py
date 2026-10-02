#!/usr/bin/env python3
"""
Tests for the database adapter (python-api/db.py).

These run against SQLite by default. To exercise the same suite against a real
Postgres (Neon) database:

    BILLING_DRIVER=postgres DATABASE_URL='postgresql://...' \\
        python3 -m pytest tests/test_billing.py -q
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

_TEST_DIR = tempfile.mkdtemp(prefix="scigraph_db_test_")
os.environ["BILLING_DB"] = str(Path(_TEST_DIR) / "db.db")
os.environ.setdefault("BILLING_DRIVER", "sqlite")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db as dbmod  # noqa: E402
import billing_store  # noqa: E402


class TranslationTests(unittest.TestCase):
    def test_placeholders_stay_on_sqlite(self):
        self.assertEqual(dbmod._translate("SELECT * FROM users WHERE email = ?"),
                         "SELECT * FROM users WHERE email = ?")

    def test_placeholders_become_percent_s_on_postgres(self):
        original = dbmod.USE_POSTGRES
        dbmod.USE_POSTGRES = True
        try:
            self.assertEqual(dbmod._translate("SELECT * FROM users WHERE email = ?"),
                             "SELECT * FROM users WHERE email = %s")
            self.assertEqual(
                dbmod._translate("UPDATE users SET a = ?, b = ? WHERE id = ?"),
                "UPDATE users SET a = %s, b = %s WHERE id = %s")
        finally:
            dbmod.USE_POSTGRES = original


class SchemaTests(unittest.TestCase):
    def test_postgres_schema_drops_autoincrement(self):
        self.assertNotIn("AUTOINCREMENT", billing_store.POSTGRES_SCHEMA)
        self.assertIn("BIGSERIAL PRIMARY KEY", billing_store.POSTGRES_SCHEMA)

    def test_postgres_schema_keeps_every_table(self):
        for table in ("users", "sessions", "subscriptions", "orders", "payments",
                      "usage_events", "search_owners", "webhook_events"):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", billing_store.POSTGRES_SCHEMA)


class QueryTests(unittest.TestCase):
    """Exercises the adapter's read/write contract on whichever driver is active."""

    @classmethod
    def setUpClass(cls):
        cls.ddl = """
        CREATE TABLE IF NOT EXISTS widgets (
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            qty INTEGER NOT NULL DEFAULT 0
        );
        """
        dbmod.db.init_schema(cls.ddl, cls.ddl.replace(
            "id INTEGER PRIMARY KEY", "id BIGSERIAL PRIMARY KEY"))

    def setUp(self):
        self.ids = []
        try:
            dbmod.db.execute("DELETE FROM widgets")
        except Exception:
            pass

    def test_insert_returns_the_new_id(self):
        new_id = dbmod.db.execute("INSERT INTO widgets (name) VALUES (?)", ("alpha",))
        self.assertGreater(new_id, 0)
        self.assertEqual(dbmod.db.query_one("SELECT name FROM widgets WHERE id = ?",
                                            (new_id,))["name"], "alpha")

    def test_query_all_returns_dicts(self):
        dbmod.db.execute("INSERT INTO widgets (name, qty) VALUES (?, ?)", ("beta", 5))
        rows = dbmod.db.query_all("SELECT * FROM widgets WHERE name = ?", ("beta",))
        self.assertEqual(len(rows), 1)
        self.assertIsInstance(rows[0], dict)
        self.assertEqual(rows[0]["qty"], 5)

    def test_query_one_returns_none_when_missing(self):
        self.assertIsNone(dbmod.db.query_one("SELECT * FROM widgets WHERE name = ?",
                                             ("ghost",)))

    def test_update_reports_rows_changed(self):
        new_id = dbmod.db.execute("INSERT INTO widgets (name, qty) VALUES (?, ?)", ("gamma", 1))
        changed = dbmod.db.execute("UPDATE widgets SET qty = ? WHERE id = ?", (9, new_id))
        self.assertEqual(changed, 1)
        self.assertEqual(dbmod.db.query_one("SELECT qty FROM widgets WHERE id = ?",
                                            (new_id,))["qty"], 9)

    def test_unique_violation_raises_the_shared_integrity_error(self):
        dbmod.db.execute("INSERT INTO widgets (name) VALUES (?)", ("delta",))
        with self.assertRaises(dbmod.INTEGRITY_ERROR):
            dbmod.db.execute("INSERT INTO widgets (name) VALUES (?)", ("delta",))

    def test_placeholders_are_actually_bound_not_interpolated(self):
        # A parameter that looks like SQL must be stored verbatim.
        dbmod.db.execute("INSERT INTO widgets (name) VALUES (?)",
                         ("'; DROP TABLE widgets; --",))
        row = dbmod.db.query_one("SELECT name FROM widgets WHERE name = ?",
                                 ("'; DROP TABLE widgets; --",))
        self.assertEqual(row["name"], "'; DROP TABLE widgets; --")

    def test_describe_reports_the_active_driver(self):
        self.assertIn(dbmod.DRIVER_NAME, dbmod.db.describe())


class UnavailableTests(unittest.TestCase):
    """The store must never block startup or crash the request path."""

    def setUp(self):
        # These exercise the Postgres path regardless of the local configuration.
        self._previous = dbmod.USE_POSTGRES
        dbmod.USE_POSTGRES = True

    def tearDown(self):
        dbmod.USE_POSTGRES = self._previous

    def _unreachable_store(self):
        class Unreachable(dbmod.Database):
            def _pg(self):
                raise dbmod.DatabaseUnavailable("unreachable")

        store = Unreachable()
        store.register_schema(
            "CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY);",
            "CREATE TABLE IF NOT EXISTS t (id BIGSERIAL PRIMARY KEY);",
        )
        return store

    def test_unreachable_database_fails_fast_and_softly(self):
        import time

        store = self._unreachable_store()

        started = time.time()
        with self.assertRaises(dbmod.DatabaseUnavailable):
            store.query_one("SELECT 1 AS ok")
        self.assertLess(time.time() - started, 1.0, "must fail fast, not hang")

        # A second attempt inside the cooldown window must not retry the network.
        started = time.time()
        with self.assertRaises(dbmod.DatabaseUnavailable):
            store.query_one("SELECT 1 AS ok")
        self.assertLess(time.time() - started, 0.5)

    def test_initialize_never_raises(self):
        import time

        store = self._unreachable_store()
        original = dbmod.db
        dbmod.db = store
        try:
            started = time.time()
            self.assertFalse(dbmod.initialize())
            self.assertLess(time.time() - started, 1.0)
        finally:
            dbmod.db = original


if __name__ == "__main__":
    unittest.main(verbosity=2)