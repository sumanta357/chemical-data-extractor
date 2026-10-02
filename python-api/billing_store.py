#!/usr/bin/env python3
"""
Billing / accounts persistence for the hosted platform.

Deliberately isolated from the existing search database (SEARCH_DB): this module
owns its own store so search state, history and exports keep working exactly as
before, with zero migration risk.

The store runs on SQLite by default and on PostgreSQL (Neon and friends) when
DATABASE_URL is set — see db.py. Every query is parameterised; there is no string
interpolation of user input.

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
"""

import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from db import INTEGRITY_ERROR, db

LOG = logging.getLogger("scigraph.billing")

BILLING_DB = Path(os.environ.get("BILLING_DB", "/tmp/scigraph_billing.db"))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime] = None) -> str:
    return (dt or _now()).isoformat()


def hash_token(token: str) -> str:
    """Session tokens are stored as SHA-256 digests, never in plaintext."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


SQLITE_SCHEMA = """
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

# Identical shape; Postgres needs a sequence-backed identity instead of
# AUTOINCREMENT and has no PRAGMA. BIGSERIAL keeps the integer ids the rest of
# the code already expects.
POSTGRES_SCHEMA = SQLITE_SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT",
                                         "BIGSERIAL PRIMARY KEY")


def init_db() -> None:
    db.init_schema(SQLITE_SCHEMA, POSTGRES_SCHEMA)


# ── Users ────────────────────────────────────────────────────────────────────

def create_user(email: str, password_hash: str, name: Optional[str] = None) -> dict:
    now = _now()
    period_end = now + timedelta(days=30)
    user_id = db.execute(
        """INSERT INTO users (email, password_hash, name, created_at, plan,
               plan_status, period_start, period_end)
           VALUES (?, ?, ?, ?, 'free', 'active', ?, ?)""",
        (email.lower(), password_hash, name, _iso(now), _iso(now), _iso(period_end)),
    )
    return get_user_by_id(user_id)


def get_user_by_email(email: str) -> Optional[dict]:
    return db.query_one("SELECT * FROM users WHERE email = ?", (email.lower(),))


def get_user_by_id(user_id: int) -> Optional[dict]:
    return db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))


def touch_last_login(user_id: int) -> None:
    db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_iso(), user_id))


def set_plan(user_id: int, plan: str, period_days: int = 30,
             status: str = "active", payment_id: Optional[str] = None,
             amount_paise: Optional[int] = None) -> dict:
    """Activate/change a plan and (re)start the usage period."""
    now = _now()
    period_end = now + timedelta(days=period_days)
    db.execute(
        """UPDATE users
              SET plan = ?, plan_status = ?, period_start = ?, period_end = ?,
                  searches_used = 0, analyst_used = 0
            WHERE id = ?""",
        (plan, status, _iso(now), _iso(period_end), user_id),
    )
    record_subscription(user_id, plan, status, payment_id, amount_paise, period_days)
    return get_user_by_id(user_id)


def record_subscription(user_id: int, plan: str, status: str,
                        payment_id: Optional[str] = None,
                        amount_paise: Optional[int] = None,
                        period_days: int = 30) -> None:
    """Audit row for plan changes (used by refunds and failed payments too)."""
    now = _now()
    db.execute(
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
    import secrets
    token = secrets.token_urlsafe(48)
    now = _now()
    db.execute(
        """INSERT INTO sessions (token_hash, user_id, created_at, expires_at, user_agent)
           VALUES (?, ?, ?, ?, ?)""",
        (hash_token(token), user_id, _iso(now),
         _iso(now + timedelta(days=ttl_days)), (user_agent or "")[:200]),
    )
    return token


def get_session_user(token: str) -> Optional[dict]:
    if not token:
        return None
    return db.query_one(
        """SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ? AND s.expires_at > ?""",
        (hash_token(token), _iso()),
    )


def delete_session(token: str) -> None:
    db.execute("DELETE FROM sessions WHERE token_hash = ?", (hash_token(token),))


def purge_expired_sessions() -> int:
    return db.execute("DELETE FROM sessions WHERE expires_at <= ?", (_iso(),))


# ── Orders & payments ────────────────────────────────────────────────────────

def create_order(order_id: str, user_id: int, plan: str, amount_paise: int,
                 receipt: str, currency: str = "INR") -> None:
    db.execute(
        """INSERT INTO orders (order_id, user_id, plan, amount_paise, currency,
               status, receipt, created_at)
           VALUES (?, ?, ?, ?, ?, 'created', ?, ?)
           ON CONFLICT(order_id) DO NOTHING""",
        (order_id, user_id, plan, amount_paise, currency, receipt, _iso()),
    )


def get_order(order_id: str) -> Optional[dict]:
    return db.query_one("SELECT * FROM orders WHERE order_id = ?", (order_id,))


def latest_unconfirmed_order(user_id: int) -> Optional[dict]:
    """Most recent order we never saw confirmed (used for reconciliation)."""
    return db.query_one(
        """SELECT order_id, plan, amount_paise, created_at FROM orders
            WHERE user_id = ? AND status = 'created'
            ORDER BY created_at DESC LIMIT 1""",
        (user_id,),
    )


def set_order_status(order_id: str, status: str) -> None:
    db.execute("UPDATE orders SET status = ? WHERE order_id = ?", (status, order_id))


def record_payment(payment_id: str, order_id: Optional[str], user_id: Optional[int],
                   plan: Optional[str], amount_paise: Optional[int], status: str,
                   method: Optional[str] = None, captured: bool = False) -> bool:
    """Insert a payment. Returns False when the payment id already exists
    (idempotent replay protection — Razorpay may deliver events more than once)."""
    try:
        db.execute(
            """INSERT INTO payments (payment_id, order_id, user_id, plan, amount_paise,
                   status, method, captured_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (payment_id, order_id, user_id, plan, amount_paise, status, method,
             _iso() if captured else None, _iso()),
        )
        return True
    except INTEGRITY_ERROR:
        return False


def get_payment(payment_id: str) -> Optional[dict]:
    return db.query_one("SELECT * FROM payments WHERE payment_id = ?", (payment_id,))


# ── Usage ────────────────────────────────────────────────────────────────────

def record_usage(user_id: int, kind: str, units: int = 1, ref: Optional[str] = None) -> None:
    db.execute(
        "INSERT INTO usage_events (user_id, kind, units, ref, created_at) VALUES (?, ?, ?, ?, ?)",
        (user_id, kind, units, ref, _iso()),
    )


def increment_counters(user_id: int, searches: int = 0, analyst: int = 0) -> None:
    db.execute(
        "UPDATE users SET searches_used = searches_used + ?, analyst_used = analyst_used + ? "
        "WHERE id = ?",
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
        db.execute(
            """UPDATE users SET period_start = ?, period_end = ?, searches_used = 0,
                   analyst_used = 0 WHERE id = ?""",
            (_iso(now), _iso(now + timedelta(days=30)), user["id"]),
        )
        return get_user_by_id(user["id"])
    return user


# ── Search ownership ─────────────────────────────────────────────────────────

def set_search_owner(search_id: str, user_id: int) -> None:
    db.execute(
        """INSERT INTO search_owners (search_id, user_id, created_at)
           VALUES (?, ?, ?) ON CONFLICT(search_id) DO NOTHING""",
        (search_id, user_id, _iso()),
    )


def claim_search_owner(search_id: str) -> Optional[int]:
    """Return the owning user_id exactly once (marks the search as counted).

    Uses a conditional UPDATE so concurrent completions of the same job cannot
    both claim it."""
    claimed = db.execute(
        "UPDATE search_owners SET counted = 1 WHERE search_id = ? AND counted = 0",
        (search_id,),
    )
    if not claimed:
        return None
    row = db.query_one("SELECT user_id FROM search_owners WHERE search_id = ?", (search_id,))
    return int(row["user_id"]) if row else None


def delete_expired_search_owners(days: int = 7) -> None:
    db.execute("DELETE FROM search_owners WHERE created_at < ?",
               (_iso(_now() - timedelta(days=days)),))


# ── Webhook dedupe ───────────────────────────────────────────────────────────

def claim_webhook_event(event_id: str, event_type: Optional[str] = None) -> bool:
    """True the first time an event id is seen; False on replay."""
    try:
        db.execute(
            "INSERT INTO webhook_events (event_id, event_type, received_at) VALUES (?, ?, ?)",
            (event_id, event_type, _iso()),
        )
        return True
    except INTEGRITY_ERROR:
        return False


# ── Reporting ────────────────────────────────────────────────────────────────

def recent_payments(user_id: int, limit: int = 20) -> list[dict]:
    return db.query_all(
        """SELECT payment_id, plan, amount_paise, status, method, captured_at, created_at
             FROM payments WHERE user_id = ? ORDER BY created_at DESC LIMIT ?""",
        (user_id, limit),
    )


def recent_usage(user_id: int, kind: str = "search", limit: int = 50) -> list[dict]:
    return db.query_all(
        """SELECT ref, created_at FROM usage_events
            WHERE user_id = ? AND kind = ? ORDER BY created_at DESC LIMIT ?""",
        (user_id, kind, limit),
    )


def stats() -> dict:
    users = db.query_one("SELECT COUNT(*) AS c FROM users") or {"c": 0}
    paid = db.query_one("SELECT COUNT(*) AS c FROM users WHERE plan <> 'free'") or {"c": 0}
    payments = db.query_one("SELECT COUNT(*) AS c FROM payments") or {"c": 0}
    return {"users": users["c"], "paying_users": paid["c"], "payments": payments["c"]}


init_db()