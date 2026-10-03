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
users            billing account + plan + usage counters, linked to Supabase
subscriptions    activation history (audit trail for refunds / plan changes)
orders           Razorpay orders we created
payments         captured / failed payments (idempotent on payment_id)
usage_events     per-unit usage ledger (searches, analyst messages)
search_owners    search_id -> user_id, so completed jobs can be attributed
webhook_events   processed webhook event ids (idempotent replay protection)

Identity (sign-up, sign-in, password reset, email confirmation) belongs to
Supabase Auth; this store only mirrors the account and owns billing.

Academic customers also carry a profile (institute, academician type, phone)
so the Academic directory can be filtered and sorted without a second store.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from db import INTEGRITY_ERROR, db, initialize

LOG = logging.getLogger("scigraph.billing")

BILLING_DB = Path(os.environ.get("BILLING_DB", "/tmp/scigraph_billing.db"))

# Supabase owns the credentials; this placeholder keeps the NOT NULL column
# satisfied. It is not a hash, so nothing can be authenticated against it.
SUPABASE_PASSWORD_PLACEHOLDER = "supabase"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: Optional[datetime] = None) -> str:
    return (dt or _now()).isoformat()


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
    razorpay_customer_id TEXT,
    email_verified_at   TEXT,
    supabase_user_id    TEXT,
    academic_email      TEXT,
    institute_name      TEXT,
    institute_id        TEXT,
    phone               TEXT,
    academician_type    TEXT
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

CREATE TABLE IF NOT EXISTS academic_verifications (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    academic_email TEXT NOT NULL,
    domain         TEXT,
    eligible       INTEGER NOT NULL DEFAULT 0,
    reason         TEXT,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_academic_verif_user ON academic_verifications(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_academic_verif_email ON academic_verifications(academic_email);

"""

# Indexes are applied *after* the column migration, never with the tables: an
# index on a column that a pre-existing `users` table does not have yet makes
# the whole schema creation fail (Postgres: undefined column). Every filter
# and sort column of the academic directory is indexed so the listing stays a
# plain indexed scan instead of a table sort.
INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_users_plan ON users(plan);
CREATE INDEX IF NOT EXISTS idx_users_institute_name ON users(institute_name);
CREATE INDEX IF NOT EXISTS idx_users_institute_id ON users(institute_id);
CREATE INDEX IF NOT EXISTS idx_users_academician_type ON users(academician_type);
CREATE INDEX IF NOT EXISTS idx_users_academic_email ON users(academic_email);
CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone);
CREATE INDEX IF NOT EXISTS idx_users_supabase ON users(supabase_user_id);
CREATE INDEX IF NOT EXISTS idx_search_owners_user ON search_owners(user_id, counted);
"""

# Identical shape; Postgres needs a sequence-backed identity instead of
# AUTOINCREMENT and has no PRAGMA. BIGSERIAL keeps the integer ids the rest of
# the code already expects.
POSTGRES_SCHEMA = SQLITE_SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT",
                                         "BIGSERIAL PRIMARY KEY")
POSTGRES_INDEX_DDL = INDEX_DDL

# Academic profile: added after the first release, so on a database that
# already has a `users` table these are applied as idempotent ALTERs.
PROFILE_COLUMNS = {
    "email_verified_at": "TEXT",
    "supabase_user_id": "TEXT",
    "academic_email": "TEXT",
    "institute_name": "TEXT",
    "institute_id": "TEXT",
    "phone": "TEXT",
    "academician_type": "TEXT",
    # Academic plan eligibility: set only by the verification endpoint, reset
    # whenever the academic address on the profile changes.
    "academic_status": "TEXT",
    "academic_verified_at": "TEXT",
    "academic_verified_email": "TEXT",
    "academic_attempts": "INTEGER",
    "academic_last_attempt_at": "TEXT",
}

# The academician categories the Academic plan is sold against.
ACADEMICIAN_TYPES = [
    "Professor",
    "Associate Professor",
    "Assistant Professor",
    "Research Scientist",
    "Research Scholar",
    "PhD Scholar",
    "Postdoctoral Researcher",
    "Lecturer",
    "Other",
]

# Columns the Academic directory may be filtered or sorted on. Whitelisted so
# a sort key can never reach SQL as free text.
DIRECTORY_FILTERS = ("academic_email", "institute_name", "institute_id",
                     "phone", "academician_type")
DIRECTORY_SORTS = DIRECTORY_FILTERS + ("email", "name", "created_at", "searches_used")


def init_db() -> None:
    """Create the schema.

    Deliberately NOT called at import time: connecting during module import
    would block container startup whenever the database is unreachable, and a
    slow or mistyped DATABASE_URL must never take the site down. The adapter
    creates the tables lazily on first use and retries after a failure.
    """
    db.register_schema(SQLITE_SCHEMA, POSTGRES_SCHEMA)
    db.register_indexes(INDEX_DDL, POSTGRES_INDEX_DDL)
    db.register_columns("users", PROFILE_COLUMNS)
    return None


def ensure_schema() -> bool:
    """Best-effort warm-up used by the startup hook. Never raises."""
    db.register_schema(SQLITE_SCHEMA, POSTGRES_SCHEMA)
    db.register_indexes(INDEX_DDL, POSTGRES_INDEX_DDL)
    db.register_columns("users", PROFILE_COLUMNS)
    return initialize()


def storage_info() -> dict:
    from db import DRIVER_NAME, USE_POSTGRES
    return {
        "driver": DRIVER_NAME,
        "location": "managed postgres" if USE_POSTGRES else str(BILLING_DB),
        "ephemeral": (not USE_POSTGRES) and str(BILLING_DB).startswith("/tmp"),
    }


# ── Users ────────────────────────────────────────────────────────────────────

def mark_email_verified(user_id: int) -> None:
    """Record that Supabase confirmed the address for this account."""
    db.execute(
        "UPDATE users SET email_verified_at = COALESCE(email_verified_at, ?) WHERE id = ?",
        (_iso(), user_id),
    )


def get_user_by_email(email: str) -> Optional[dict]:
    return db.query_one("SELECT * FROM users WHERE email = ?", (email.lower(),))


def get_user_by_id(user_id: int) -> Optional[dict]:
    return db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))


def touch_last_login(user_id: int) -> None:
    db.execute("UPDATE users SET last_login_at = ? WHERE id = ?", (_iso(), user_id))


# ── Supabase-linked accounts ─────────────────────────────────────────────────

def get_user_by_supabase_id(sub: str) -> Optional[dict]:
    if not sub:
        return None
    return db.query_one("SELECT * FROM users WHERE supabase_user_id = ?", (sub,))


def create_supabase_user(sub: str, email: str, name: Optional[str] = None,
                         email_verified: bool = False) -> dict:
    """Create the billing account for a Supabase user on first sight.

    There is no password: authentication lives in Supabase, so the column
    keeps a placeholder that is not a valid hash."""
    now = _now()
    user_id = db.execute(
        """INSERT INTO users (email, password_hash, name, created_at, plan,
               plan_status, period_start, period_end, supabase_user_id,
               email_verified_at)
           VALUES (?, ?, ?, ?, 'free', 'active', ?, ?, ?, ?)""",
        (email.lower(), SUPABASE_PASSWORD_PLACEHOLDER, name, _iso(now),
         _iso(now), _iso(now + timedelta(days=30)), sub,
         _iso(now) if email_verified else None),
    )
    return get_user_by_id(user_id)


def update_identity(user_id: int, changes: dict) -> None:
    """Mirror the details Supabase owns (name, email)."""
    allowed = ("name", "email")
    updates = {k: v for k, v in changes.items() if k in allowed}
    if not updates:
        return
    assignments = ", ".join(f"{column} = ?" for column in updates)
    db.execute(f"UPDATE users SET {assignments} WHERE id = ?",
               tuple(updates.values()) + (user_id,))


# ── Academic profile ─────────────────────────────────────────────────────────

def get_profile(user_id: int) -> dict:
    user = get_user_by_id(user_id) or {}
    return {
        "email": user.get("email"),
        "name": user.get("name"),
        "academic_email": user.get("academic_email"),
        "institute_name": user.get("institute_name"),
        "institute_id": user.get("institute_id"),
        "phone": user.get("phone"),
        "academician_type": user.get("academician_type"),
        "academic_status": user.get("academic_status") or "none",
        "academic_verified_at": user.get("academic_verified_at"),
        "academic_verified_email": user.get("academic_verified_email"),
        "plan": user.get("plan", "free"),
        "email_verified": bool(user.get("email_verified_at")),
    }


def update_profile(user_id: int, fields: dict) -> dict:
    """Write the academic profile. `fields` is already validated by the caller
    and limited to the profile columns, so no key reaches SQL from a request."""
    allowed = ("name", "academic_email", "institute_name", "institute_id",
               "phone", "academician_type")
    updates = {k: v for k, v in fields.items() if k in allowed}
    if updates:
        assignments = ", ".join(f"{column} = ?" for column in updates)
        db.execute(
            f"UPDATE users SET {assignments} WHERE id = ?",
            tuple(updates.values()) + (user_id,),
        )
        # Changing the academic address invalidates any verification that was
        # granted for the previous one. Without this, a verified user could keep
        # their Academic plan while pointing the profile at a new address, and
        # a fresh sign-up could carry over a stale 'verified' status.
        if "academic_email" in updates:
            current = get_user_by_id(user_id) or {}
            if (current.get("academic_verified_email") or "").strip().lower() != \
                    str(updates["academic_email"]).strip().lower():
                clear_academic_verification(user_id)
    return get_profile(user_id)


# ── Academic verification ────────────────────────────────────────────────────

def set_academic_status(user_id: int, status: str, verified_email: Optional[str] = None) -> dict:
    """Set the verification state. `verified` stamps the address and time;
    anything else clears both so a stale address can never be acted on."""
    now = _iso()
    if status == "verified":
        db.execute(
            """UPDATE users SET academic_status = ?, academic_verified_at = ?,
                   academic_verified_email = ?, academic_attempts = academic_attempts + 1,
                   academic_last_attempt_at = ?
                WHERE id = ?""",
            ("verified", now, verified_email, now, user_id),
        )
    else:
        db.execute(
            """UPDATE users SET academic_status = ?, academic_verified_at = NULL,
                   academic_verified_email = NULL, academic_last_attempt_at = ?
                WHERE id = ?""",
            (status, now, user_id),
        )
    return get_user_by_id(user_id) or {}


def clear_academic_verification(user_id: int) -> None:
    set_academic_status(user_id, "pending")


def count_academic_attempts_since(user_id: int, since_iso: str) -> int:
    """Attempts in the window, used to rate-limit verification requests."""
    row = db.query_one(
        """SELECT COUNT(*) AS n FROM academic_verifications
            WHERE user_id = ? AND created_at >= ?""",
        (user_id, since_iso),
    )
    return int(row["n"]) if row else 0


def record_academic_attempt(user_id: int, academic_email: str, domain: str,
                            eligible: bool, reason: str) -> None:
    """Audit row for every verification attempt, successful or not."""
    db.execute(
        """INSERT INTO academic_verifications
               (user_id, academic_email, domain, eligible, reason, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, academic_email, domain, 1 if eligible else 0, reason, _iso()),
    )


def academic_email_claimed_by_other(academic_email: str, user_id: int) -> bool:
    """True when a *different* already-verified account holds this address.
    One institutional address, one Academic subscription."""
    row = db.query_one(
        """SELECT id FROM users
            WHERE academic_verified_email = ? AND academic_status = 'verified'
              AND id != ? LIMIT 1""",
        (academic_email.strip().lower(), user_id),
    )
    return row is not None


# ── Academic directory ───────────────────────────────────────────────────────

def _ilike() -> str:
    """Case-insensitive match: ILIKE on Postgres, LIKE on SQLite."""
    from db import USE_POSTGRES
    return "ILIKE" if USE_POSTGRES else "LIKE"


def list_academic_customers(plan: str = "academic", q: Optional[str] = None,
                            academic_email: Optional[str] = None,
                            institute_name: Optional[str] = None,
                            institute_id: Optional[str] = None,
                            phone: Optional[str] = None,
                            academician_type: Optional[str] = None,
                            verified: Optional[bool] = None,
                            sort: str = "created_at", order: str = "desc",
                            limit: int = 50, offset: int = 0) -> dict:
    """Filter + sort the academic customers. Every filter is a bound
    parameter and the sort column is whitelisted, so this stays a plain
    indexed query on either driver."""
    if sort not in DIRECTORY_SORTS:
        sort = "created_at"
    direction = "ASC" if str(order).lower() == "asc" else "DESC"
    like = _ilike()

    where = []
    params: list = []
    if plan:
        where.append("plan = ?")
        params.append(plan)
    for column, value in (("academic_email", academic_email),
                          ("institute_name", institute_name),
                          ("institute_id", institute_id),
                          ("phone", phone),
                          ("academician_type", academician_type)):
        if value:
            where.append(f"{column} {like} ?")
            params.append(f"%{value}%")
    if q:
        clause = " OR ".join(
            f"{column} {like} ?" for column in
            ("email", "academic_email", "institute_name", "institute_id",
             "phone", "academician_type", "name")
        )
        where.append(f"({clause})")
        params.extend([f"%{q}%"] * 7)
    if verified is not None:
        where.append("email_verified_at IS NOT NULL" if verified
                     else "email_verified_at IS NULL")

    sql_where = f" WHERE {' AND '.join(where)}" if where else ""
    total = db.query_one(f"SELECT COUNT(*) AS n FROM users{sql_where}", tuple(params))
    rows = db.query_all(
        f"""SELECT id, email, name, academic_email, institute_name, institute_id,
                   phone, academician_type, plan, plan_status, searches_used,
                   created_at, last_login_at, email_verified_at
              FROM users{sql_where}
          ORDER BY {sort} {direction}, id {direction}
             LIMIT ? OFFSET ?""",
        tuple(params) + (max(1, min(int(limit), 200)), max(0, int(offset))),
    )
    return {"academics": rows, "total": int(total["n"]) if total else 0,
            "limit": limit, "offset": offset, "sort": sort, "order": direction.lower()}


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
    db.run(
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


def count_inflight_searches(user_id: int, since_iso: Optional[str] = None) -> int:
    """Searches this user owns that have not been counted yet.

    A search is only added to `searches_used` when it *completes*, so between
    starting a job and it finishing the user could otherwise open an unbounded
    number of parallel searches and never trip the quota. Counting in-flight
    work makes the limit reflect what has actually been started.

    Bounded to the current period so a job left over from a previous cycle (or
    from before a plan upgrade) cannot consume the new allowance.
    """
    if since_iso:
        row = db.query_one(
            """SELECT COUNT(*) AS n FROM search_owners
                WHERE user_id = ? AND counted = 0 AND created_at >= ?""",
            (user_id, since_iso),
        )
    else:
        row = db.query_one(
            "SELECT COUNT(*) AS n FROM search_owners WHERE user_id = ? AND counted = 0",
            (user_id,),
        )
    return int(row["n"]) if row else 0


def delete_expired_search_owners(days: int = 7) -> None:
    db.execute("DELETE FROM search_owners WHERE created_at < ?",
               (_iso(_now() - timedelta(days=days)),))


# ── Webhook dedupe ───────────────────────────────────────────────────────────

def claim_webhook_event(event_id: str, event_type: Optional[str] = None) -> bool:
    """True the first time an event id is seen; False on replay."""
    try:
        db.run(
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


# Register the schema only — no connection is opened at import time.
init_db()