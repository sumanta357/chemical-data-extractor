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
import socket
import sqlite3
import threading
import time
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


def _scrub(message: str) -> str:
    """Remove the password from a connection error before it reaches a log."""
    if "://" in DATABASE_URL and "@" in DATABASE_URL:
        creds = DATABASE_URL.split("://", 1)[1].split("@", 1)[0]
        if ":" in creds:
            password = creds.split(":", 1)[1]
            if password:
                message = message.replace(password, "***")
    return message


class DatabaseUnavailable(RuntimeError):
    """The store could not be reached. Callers decide whether to degrade."""


class Database:
    """Thin synchronous wrapper. Account traffic is low-volume, so a single
    lock-protected connection with automatic reconnect is sufficient and avoids
    pulling in a pooling dependency."""

    def __init__(self) -> None:
        # Reentrant: closing a connection and creating the schema both happen
        # while a query already holds this lock. A plain Lock would deadlock and
        # hang the request thread forever the first time a query failed.
        self._lock = threading.RLock()
        self._conn = None
        self._path = os.environ.get("BILLING_DB", "/tmp/scigraph_billing.db")
        self._sqlite_ddl = ""
        self._postgres_ddl = ""
        self._schema_ready = False
        self._retry_after = 0.0

    # ── schema (created lazily on first use) ──────────────────────────────
    def register_schema(self, sqlite_ddl: str, postgres_ddl: str) -> None:
        """Hand the DDL to the adapter. Nothing is executed here, so importing
        this module never touches the network — the container can always boot
        even if the database is unreachable."""
        self._sqlite_ddl = sqlite_ddl
        self._postgres_ddl = postgres_ddl

    def _ensure_ready(self) -> None:
        if self._schema_ready:
            return
        if self._retry_after and time.time() < self._retry_after:
            # A previous attempt failed; fail fast instead of blocking boot.
            raise DatabaseUnavailable("store is temporarily unavailable, retrying shortly")
        try:
            self.init_schema(self._sqlite_ddl, self._postgres_ddl)
            self._schema_ready = True
            self._retry_after = 0.0
        except DatabaseUnavailable as exc:
            self._retry_after = time.time() + 20
            self._last_error = str(exc)
            raise
        except Exception as exc:
            self._retry_after = time.time() + 20
            self._last_error = _scrub(str(exc))[:300]
            raise DatabaseUnavailable(f"could not initialise the accounts store: {exc}")

    # ── connection handling ───────────────────────────────────────────────
    def _sqlite(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    @staticmethod
    def _connect_variants() -> list[dict]:
        """Connection attempts in order of preference.

        Neon (and most managed Postgres) publish both A and AAAA records. Hosts
        without IPv6 routing — Render's containers among them — fail
        immediately when libpq resolves the AAAA record first, so prefer an
        explicit IPv4 address via libpq's `hostaddr` and keep the plain
        connection as the final fallback.
        """
        variants: list[dict] = []

        override = os.environ.get("DATABASE_HOSTADDR", "").strip()
        if override:
            variants.append({"hostaddr": override})

        try:
            from urllib.parse import urlsplit
            host = urlsplit(DATABASE_URL).hostname
            if host:
                for info in socket.getaddrinfo(host, 5432, socket.AF_INET)[:1]:
                    variants.append({"hostaddr": info[4][0]})
        except Exception:
            pass

        variants.append({})
        return variants

    def _pg(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
        except Exception as exc:
            raise DatabaseUnavailable(f"psycopg is not installed: {exc}")
        try:
            base = {
                "row_factory": dict_row,
                "connect_timeout": 10,
                "application_name": "scigraph-api",
                "options": "-c statement_timeout=15000",
            }
        except Exception as exc:  # pragma: no cover - defensive
            raise DatabaseUnavailable(f"psycopg is not usable: {exc}")

        attempts: list[str] = []
        for extra in self._connect_variants():
            label = extra.get("hostaddr", "default")
            try:
                return psycopg.connect(DATABASE_URL, **{**base, **extra})
            except Exception as exc:
                # Report enough to diagnose (host, port, TLS) but never the password.
                attempts.append(f"{label}: {_scrub(str(exc)).strip().replace(chr(10), ' ')[:200]}")

        detail = " | ".join(attempts)[-300:] if attempts else "unknown"
        raise DatabaseUnavailable(f"could not reach the configured database: {detail}")

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
            self._ensure_ready()
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
            self._ensure_ready()
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
                except Exception as exc:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    raise DatabaseUnavailable(
                        f"could not create the accounts schema ({exc.__class__.__name__})"
                    )
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


def status() -> dict:
    """Safe-to-expose health of the store (no credentials, no host details)."""
    reachable = ping()
    return {
        "driver": DRIVER_NAME,
        "reachable": reachable,
        "error": None if reachable else (_last_error() or "unavailable"),
    }


def _last_error() -> Optional[str]:
    return getattr(db, "_last_error", None)


def initialize() -> bool:
    """Create the schema up-front if possible. Never raises: the container must
    boot even when the database is unreachable, and the first request will
    retry. Returns True when the store is ready."""
    try:
        db._ensure_ready()
        return True
    except Exception:
        return False