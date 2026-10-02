#!/usr/bin/env python3
"""
Accounts, sessions and password security.

Uses only the standard library (hashlib/hmac/secrets) so no new dependency is
added to the container. Passwords are stored as PBKDF2-HMAC-SHA256 with a
per-user random salt and a high iteration count; session tokens are opaque
random strings stored as SHA-256 digests.
"""

import hashlib
import hmac
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, Request, Response

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import billing_store  # noqa: E402

COOKIE_NAME = "scigraph_session"
PBKDF2_ITERATIONS = 210_000
SESSION_TTL_DAYS = int(os.environ.get("SESSION_TTL_DAYS", "30"))
# Secure cookies are on by default (HTTPS). Set COOKIE_SECURE=0 for local http testing.
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "1") not in ("0", "false", "False")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")


# ── Passwords ────────────────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_hex, digest_hex = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iterations)
        )
        return hmac.compare_digest(expected, actual)
    except (ValueError, AttributeError, TypeError):
        return False


def validate_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 254:
        raise HTTPException(status_code=400, detail="Enter a valid email address")
    return email


def validate_password(password: str) -> str:
    if not password or len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if len(password) > 200:
        raise HTTPException(status_code=400, detail="Password is too long")
    return password


# ── Session cookie ───────────────────────────────────────────────────────────

def _token_from_request(request: Request) -> Optional[str]:
    token = request.cookies.get(COOKIE_NAME)
    if token:
        return token
    # Fallback for API clients that cannot hold cookies (curl, server-to-server).
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_TTL_DAYS * 24 * 3600,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def login(request: Request, response: Response, email: str, password: str) -> dict:
    user = billing_store.get_user_by_email(email)
    if not user or not verify_password(password, user["password_hash"]):
        # Same message for unknown account and wrong password — no user enumeration.
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    token = billing_store.create_session(
        user["id"], request.headers.get("user-agent"), SESSION_TTL_DAYS
    )
    billing_store.touch_last_login(user["id"])
    set_session_cookie(response, token)
    return billing_store.get_user_by_id(user["id"])


def register(request: Request, response: Response, email: str,
             password: str, name: Optional[str] = None) -> dict:
    email = validate_email(email)
    validate_password(password)
    if billing_store.get_user_by_email(email):
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    user = billing_store.create_user(email, hash_password(password), name)
    token = billing_store.create_session(
        user["id"], request.headers.get("user-agent"), SESSION_TTL_DAYS
    )
    set_session_cookie(response, token)
    return billing_store.get_user_by_id(user["id"])


def logout(request: Request, response: Response) -> None:
    token = _token_from_request(request)
    if token:
        billing_store.delete_session(token)
    clear_session_cookie(response)


# ── Dependencies ─────────────────────────────────────────────────────────────

def current_user_optional(request: Request) -> Optional[dict]:
    """Never raises — used on endpoints that must keep working for anonymous
    visitors (search submit stays open by default)."""
    token = _token_from_request(request)
    if not token:
        return None
    user = billing_store.get_session_user(token)
    if user:
        return billing_store.reset_period_if_due(user)
    return None


def current_user_required(request: Request) -> dict:
    user = current_user_optional(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in to continue")
    return user