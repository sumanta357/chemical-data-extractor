#!/usr/bin/env python3
"""
Database adapter for the accounts store.

The platform ships with two interchangeable backends:

* **SQLite** (default) — a file on disk, zero configuration. Used when
  DATABASE_URL is not set.
* **PostgreSQL** — used when DATABASE_URL points at a serverless Postgres
  such as Neon. Survives redeploys without needing a persistent disk, and is
  shared if the app ever runs on more than one container.

Both backends expose the same four calls (query_all / query_one / execute /
init_schema), return plain dicts, and accept `?` placeholders so the calling
code in billing_store.py is identical on either backend.
"""

import os
import sqlite3
import threading
from typing import Any, Optional

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()

# "auto" (default) picks Postgres when DATABASE_URL is present.
_requested = os.environ.get("BILLING_DRIVER", "auto").strip().lower()
if _requested in ("postgres", "postgresql", "neon"):
    USE_POSTGRES = True
elif _requested in ("sqlite", "sqlite3", "file"):
    USE_POSTGRES = False
else:
    USE_POSTGRES = bool(DATABASE_URL)

DRIVER_NAME = "postgres" if USE_POSTGRES else "sqlite"

# Raised when a unique constraint is violated (duplicate payment / webhook id).
if USE_POSTGRES:
    try:
        from psycopg import errors as _pg_errors
        INTEGRITY_ERROR: tuple = (_pg_errors.UniqueViolation,)
    except Exception:  # pragma: no cover - psycopg missing
        INTEGRITY_ERROR = (Exception,)
else:
    INTEGRITY_ERROR = (sqlite3.IntegrityError,)


def _translate(sql: str) -> str:
    """Rewrite `?` placeholders as `%s` for the Postgres driver."""
    return sql.replace("?", "%s") if USE_POSTGRES else sql


class Database:
    """Thin synchronous wrapper. Account traffic is low-volume, so a single
    lock-protected connection with automatic reconnect is sufficient and avoids
    pulling in a pooling dependency."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._conn = None
        self._path = os.environ.get("BILLING_DB", "/tmp/scigraph_billing.db")

    # ── connection handling ───────────────────────────────────────────────
    def _sqlite(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _pg(self):
        import psycopg
        from psycopg.rows import dict_row
        return psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=15)

    def _connection(self):
        if not USE_POSTGRES:
            return self._sqlite(), False
        if self._conn is None or self._conn.closed:
            self._conn = self._pg()
        return self._conn, True

    def close(self) -> None:
        with self._lock:
            if USE_POSTGRES and self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

    # ── reads ─────────────────────────────────────────────────────────────
    def query_all(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            conn, shared = self._connection()
            cur = conn.cursor()
            try:
                cur.execute(_translate(sql), params)
                rows = cur.fetchall()
                if not shared:
                    conn.close()
                return [dict(r) for r in rows]
            except Exception:
                if shared:
                    self.close()
                else:
                    conn.close()
                raise
            finally:
                try:
                    cur.close()
                except Exception:
                    pass

    def query_one(self, sql: str, params: tuple = ()) -> Optional[dict]:
        rows = self.query_all(sql, params)
        return rows[0] if rows else None

    # ── writes ────────────────────────────────────────────────────────────
    def execute(self, sql: str, params: tuple = ()) -> int:
        """Run a write. Returns the new row id for INSERT, else the rowcount."""
        statement = sql.strip()
        wants_id = statement.upper().startswith("INSERT") and "RETURNING" not in statement.upper()
        if USE_POSTGRES and wants_id:
            statement += " RETURNING id"

        with self._lock:
            conn, shared = self._connection()
            cur = conn.cursor()
            try:
                cur.execute(_translate(statement), params)
                if wants_id:
                    if USE_POSTGRES:
                        row = cur.fetchone()
                        data = dict(row) if row else {}
                        result = int(data["id"]) if "id" in data else 0
                    else:
                        result = int(cur.lastrowid or 0)
                else:
                    result = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
                conn.commit()
                if not shared:
                    conn.close()
                return result
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                if shared:
                    self.close()
                else:
                    conn.close()
                raise
            finally:
                try:
                    cur.close()
                except Exception:
                    pass

    def init_schema(self, sqlite_ddl: str, postgres_ddl: str) -> None:
        if USE_POSTGRES:
            with self._lock:
                conn = self._pg()
                cur = conn.cursor()
                try:
                    for statement in postgres_ddl.split(";"):
                        if statement.strip():
                            cur.execute(statement)
                    conn.commit()
                finally:
                    try:
                        cur.close()
                    except Exception:
                        pass
                    conn.close()
            return

        import pathlib
        parent = pathlib.Path(self._path).parent
        parent.mkdir(parents=True, exist_ok=True)
        conn = self._sqlite()
        try:
            conn.executescript(sqlite_ddl)
            conn.commit()
        finally:
            conn.close()

    def describe(self) -> str:
        return f"{DRIVER_NAME}:{DATABASE_URL.split('@')[-1] if USE_POSTGRES else self._path}"


db = Database()


def ping() -> bool:
    """Cheap health check used by the startup banner."""
    try:
        db.query_one("SELECT 1 AS ok")
        return True
    except Exception:
        return False