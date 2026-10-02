#!/usr/bin/env python3
"""
Billing / accounts persistence for the hosted platform.

Deliberately isolated from the existing search database (SEARCH_DB): this module
owns its own SQLite file so search state, history and exports keep working
exactly as before, with zero migration risk.

Tables
------
users            account + plan + current usage counters
sessions         opaque session tokens (hashed at rest)
subscriptions    activation history (audit trail for refunds / plan changes)
orders           Razorpay orders we created
payments         captured / failed payments (idempotent on payment_id)
usage_events     per-unit usage ledger (searches, analyst messages)
search_owners    search_id -> user_id, so completed jobs can be attributed
webhook_events   processed webhook event ids (idempotent replay protection)

All queries are parameterised; there is no string interpolation of user input.
"""

import hashlib
import json
import logging
import os
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

LOG = logging.getLogger("scigraph.billing")

BILLING_DB = Path(os.environ.get("BILLING_DB", "/tmp/scigraph_billing.db"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime] = None) -> str:
    return (dt or _now()).isoformat()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(BILLING_DB), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def hash_token(token: str) -> str:
    """Session tokens are stored as SHA-256 digests, never in plaintext."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    email               TEXT UNIQUE NOT NULL,
    password_hash       TEXT NOT NULL,
    name                TEXT,
    created_at          TEXT NOT NULL,
    last_login_at       TEXT,
    plan                TEXT NOT NULL DEFAULT 'free',
    plan_status         TEXT NOT NULL DEFAULT 'active',
    period_start        TEXT NOT NULL,
    period_end          TEXT NOT NULL,
    searches_used       INTEGER NOT NULL DEFAULT 0,
    analyst_used        INTEGER NOT NULL DEFAULT 0,
    razorpay_customer_id TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    user_agent TEXT
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id              INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan                 TEXT NOT NULL,
    status               TEXT NOT NULL,
    razorpay_payment_id  TEXT,
    amount_paise         INTEGER,
    started_at           TEXT NOT NULL,
    current_period_end   TEXT,
    cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
    created_at           TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id     TEXT UNIQUE NOT NULL,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan         TEXT NOT NULL,
    amount_paise INTEGER NOT NULL,
    currency     TEXT NOT NULL DEFAULT 'INR',
    status       TEXT NOT NULL DEFAULT 'created',
    receipt      TEXT,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS payments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_id   TEXT UNIQUE NOT NULL,
    order_id     TEXT,
    user_id      INTEGER REFERENCES users(id) ON DELETE SET NULL,
    plan         TEXT,
    amount_paise INTEGER,
    currency     TEXT NOT NULL DEFAULT 'INR',
    status       TEXT NOT NULL,
    method       TEXT,
    captured_at  TEXT,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    units      INTEGER NOT NULL DEFAULT 1,
    ref        TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage_user ON usage_events(user_id, kind, created_at);

CREATE TABLE IF NOT EXISTS search_owners (
    search_id  TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    counted    INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS webhook_events (
    event_id    TEXT PRIMARY KEY,
    event_type  TEXT,
    received_at TEXT NOT NULL
);
"""


def init_db() -> None:
    BILLING_DB.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(SCHEMA)


# ── Users ────────────────────────────────────────────────────────────────────

def create_user(email: str, password_hash: str, name: Optional[str] = None) -> dict:
    now = _now()
    period_end = now + timedelta(days=30)
    with _connect() as conn:
        cur = conn.execute(
            """INSERT INTO users (email, password_hash, name, created_at, plan,
                   plan_status, period_start, period_end)
               VALUES (?, ?, ?, ?, 'free', 'active', ?, ?)""",
            (email.lower(), password_hash, name, _iso(now), _iso(now), _iso(period_end)),
        )
        user_id = cur.lastrowid
    return get_user_by_id(user_id)


def get_user_by_email(email: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE email = ?", (email.lower(),)).fetchone()
    return dict(row) if row else None


def get_user_by_id(user_id: int) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def touch_last_login(user_id: int) -> None:
    with _connect() as conn:
        conn.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_iso(), user_id))


def set_plan(user_id: int, plan: str, period_days: int = 30,
             status: str = "active", payment_id: Optional[str] = None,
             amount_paise: Optional[int] = None) -> dict:
    """Activate/change a plan and (re)start the usage period."""
    now = _now()
    period_end = now + timedelta(days=period_days)
    with _connect() as conn:
        conn.execute(
            """UPDATE users
                  SET plan = ?, plan_status = ?, period_start = ?, period_end = ?,
                      searches_used = 0, analyst_used = 0
                WHERE id = ?""",
            (plan, status, _iso(now), _iso(period_end), user_id),
        )
        conn.execute(
            """INSERT INTO subscriptions (user_id, plan, status, razorpay_payment_id,
                   amount_paise, started_at, current_period_end, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, plan, status, payment_id, amount_paise,
             _iso(now), _iso(period_end), _iso(now)),
        )
    return get_user_by_id(user_id)


def record_subscription(user_id: int, plan: str, status: str,
                        payment_id: Optional[str] = None,
                        amount_paise: Optional[int] = None,
                        period_days: int = 30) -> None:
    """Audit-only row (used for failed payments / refunds — no plan change)."""
    now = _now()
    with _connect() as conn:
        conn.execute(
            """INSERT INTO subscriptions (user_id, plan, status, razorpay_payment_id,
                   amount_paise, started_at, current_period_end, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, plan, status, payment_id, amount_paise,
             _iso(now), _iso(now + timedelta(days=period_days)), _iso(now)),
        )


# ── Sessions ─────────────────────────────────────────────────────────────────

def create_session(user_id: int, user_agent: Optional[str] = None,
                   ttl_days: int = 30) -> str:
    """Create a session and return the raw token (only time it is seen)."""
    token = secrets.token_urlsafe(48)
    now = _now()
    with _connect() as conn:
        conn.execute(
            """INSERT INTO sessions (token_hash, user_id, created_at, expires_at, user_agent)
               VALUES (?, ?, ?, ?, ?)""",
            (hash_token(token), user_id, _iso(now),
             _iso(now + timedelta(days=ttl_days)), (user_agent or "")[:200]),
        )
    return token


def get_session_user(token: str) -> Optional[dict]:
    if not token:
        return None
    now = _iso()
    with _connect() as conn:
        row = conn.execute(
            """SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = ? AND s.expires_at > ?""",
            (hash_token(token), now),
        ).fetchone()
    return dict(row) if row else None


def delete_session(token: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_token(token),))


def purge_expired_sessions() -> int:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (_iso(),))
        return cur.rowcount or 0


# ── Orders & payments ────────────────────────────────────────────────────────

def create_order(order_id: str, user_id: int, plan: str, amount_paise: int,
                 receipt: str, currency: str = "INR") -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO orders (order_id, user_id, plan, amount_paise, currency,
                   status, receipt, created_at)
               VALUES (?, ?, ?, ?, ?, 'created', ?, ?)
               ON CONFLICT(order_id) DO NOTHING""",
            (order_id, user_id, plan, amount_paise, currency, receipt, _iso()),
        )


def get_order(order_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
    return dict(row) if row else None


def set_order_status(order_id: str, status: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE orders SET status = ? WHERE order_id = ?", (status, order_id))


def record_payment(payment_id: str, order_id: Optional[str], user_id: Optional[int],
                   plan: Optional[str], amount_paise: Optional[int], status: str,
                   method: Optional[str] = None, captured: bool = False) -> bool:
    """Insert a payment. Returns False when the payment id already exists
    (idempotent replay protection — Razorpay may deliver events more than once)."""
    with _connect() as conn:
        try:
            conn.execute(
                """INSERT INTO payments (payment_id, order_id, user_id, plan, amount_paise,
                       status, method, captured_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (payment_id, order_id, user_id, plan, amount_paise, status, method,
                 _iso() if captured else None, _iso()),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def get_payment(payment_id: str) -> Optional[dict]:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM payments WHERE payment_id = ?", (payment_id,)).fetchone()
    return dict(row) if row else None


# ── Usage ────────────────────────────────────────────────────────────────────

def record_usage(user_id: int, kind: str, units: int = 1, ref: Optional[str] = None) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT INTO usage_events (user_id, kind, units, ref, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_id, kind, units, ref, _iso()),
        )


def increment_counters(user_id: int, searches: int = 0, analyst: int = 0) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE users SET searches_used = searches_used + ?, analyst_used = analyst_used + ? WHERE id = ?",
            (searches, analyst, user_id),
        )


def reset_period_if_due(user: dict) -> dict:
    """Roll the usage window over when the billing period has elapsed."""
    if not user:
        return user
    try:
        end = datetime.fromisoformat(user["period_end"])
    except (KeyError, TypeError, ValueError):
        return user
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    if _now() >= end:
        now = _now()
        with _connect() as conn:
            conn.execute(
                """UPDATE users SET period_start = ?, period_end = ?, searches_used = 0,
                       analyst_used = 0 WHERE id = ?""",
                (_iso(now), _iso(now + timedelta(days=30)), user["id"]),
            )
        return get_user_by_id(user["id"])
    return user


# ── Search ownership ─────────────────────────────────────────────────────────

def set_search_owner(search_id: str, user_id: int) -> None:
    with _connect() as conn:
        conn.execute(
            """INSERT INTO search_owners (search_id, user_id, created_at)
               VALUES (?, ?, ?) ON CONFLICT(search_id) DO NOTHING""",
            (search_id, user_id, _iso()),
        )


def claim_search_owner(search_id: str) -> Optional[int]:
    """Return the owning user_id exactly once (marks the search as counted)."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT user_id FROM search_owners WHERE search_id = ? AND counted = 0",
            (search_id,),
        ).fetchone()
        if not row:
            return None
        conn.execute("UPDATE search_owners SET counted = 1 WHERE search_id = ?", (search_id,))
        return int(row["user_id"])


def delete_expired_search_owners(days: int = 7) -> None:
    cutoff = _iso(_now() - timedelta(days=days))
    with _connect() as conn:
        conn.execute("DELETE FROM search_owners WHERE created_at < ?", (cutoff,))


# ── Webhook dedupe ───────────────────────────────────────────────────────────

def claim_webhook_event(event_id: str, event_type: Optional[str] = None) -> bool:
    """True the first time an event id is seen; False on replay."""
    with _connect() as conn:
        try:
            conn.execute(
                "INSERT INTO webhook_events (event_id, event_type, received_at) VALUES (?, ?, ?)",
                (event_id, event_type, _iso()),
            )
            return True
        except sqlite3.IntegrityError:
            return False


def recent_payments(user_id: int, limit: int = 20) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT payment_id, plan, amount_paise, status, method, captured_at, created_at
                 FROM payments WHERE user_id = ? ORDER BY created_at DESC LIMIT ?""",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def recent_usage(user_id: int, kind: str = "search", limit: int = 50) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            """SELECT ref, created_at FROM usage_events
                WHERE user_id = ? AND kind = ? ORDER BY created_at DESC LIMIT ?""",
            (user_id, kind, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def stats() -> dict:
    with _connect() as conn:
        users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        paid = conn.execute(
            "SELECT COUNT(*) c FROM users WHERE plan != 'free'"
        ).fetchone()["c"]
        payments = conn.execute("SELECT COUNT(*) c FROM payments").fetchone()["c"]
    return {"users": users, "paying_users": paid, "payments": payments}


init_db()