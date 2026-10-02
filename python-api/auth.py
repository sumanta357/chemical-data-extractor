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
import logging
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
import emailer  # noqa: E402

# Opt-in: when 1, an account must confirm its email address before it can sign
# in. Off by default so existing accounts keep working untouched.
REQUIRE_VERIFIED_EMAIL = os.environ.get(
    "REQUIRE_VERIFIED_EMAIL", "0"
).strip().lower() in ("1", "true", "yes", "on")

VERIFY_TOKEN_TTL_MINUTES = int(os.environ.get("VERIFY_TOKEN_TTL_MINUTES", "1440"))
RESET_TOKEN_TTL_MINUTES = int(os.environ.get("RESET_TOKEN_TTL_MINUTES", "60"))
RESET_REQUESTS_PER_HOUR = int(os.environ.get("RESET_REQUESTS_PER_HOUR", "5"))

COOKIE_NAME = "scigraph_session"
LOG = logging.getLogger("scigraph.auth")
PBKDF2_ITERATIONS = 210_000
SESSION_TTL_DAYS = int(os.environ.get("SESSION_TTL_DAYS", "30"))
# Secure cookies are on by default (HTTPS). Set COOKIE_SECURE=0 for local http testing.
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "1") not in ("0", "false", "False")

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9 ()-]{5,24}$")
MAX_FIELD_LEN = 200


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


# ── Academic profile validation ──────────────────────────────────────────────

def _clean(value: Optional[str], limit: int = MAX_FIELD_LEN) -> str:
    return (value or "").strip()[:limit]


def validate_profile(fields: dict) -> dict:
    """Normalise the academic profile. Returns only known keys so nothing
    unexpected can reach the UPDATE statement."""
    clean: dict = {}

    name = _clean(fields.get("name"), 120)
    if name:
        clean["name"] = name

    academic_email = _clean(fields.get("academic_email"))
    if academic_email:
        clean["academic_email"] = validate_email(academic_email)

    institute_name = _clean(fields.get("institute_name"))
    if institute_name:
        clean["institute_name"] = institute_name

    institute_id = _clean(fields.get("institute_id"), 80)
    if institute_id:
        clean["institute_id"] = institute_id

    phone = _clean(fields.get("phone"), 32)
    if phone:
        if not PHONE_RE.match(phone):
            raise HTTPException(status_code=400, detail="Enter a valid phone number")
        clean["phone"] = phone

    academician_type = _clean(fields.get("academician_type"), 60)
    if academician_type:
        allowed = billing_store.ACADEMICIAN_TYPES
        if academician_type not in allowed:
            raise HTTPException(
                status_code=400,
                detail={"message": "Choose a valid academician type",
                        "allowed": allowed},
            )
        clean["academician_type"] = academician_type

    return clean


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
    if REQUIRE_VERIFIED_EMAIL and not user.get("email_verified_at"):
        raise HTTPException(
            status_code=403,
            detail={"message": "Confirm your email address before signing in.",
                    "code": "email_unverified"},
        )
    token = billing_store.create_session(
        user["id"], request.headers.get("user-agent"), SESSION_TTL_DAYS
    )
    billing_store.touch_last_login(user["id"])
    set_session_cookie(response, token)
    return billing_store.get_user_by_id(user["id"])


def register(request: Request, response: Response, email: str,
             password: str, name: Optional[str] = None,
             profile: Optional[dict] = None) -> dict:
    email = validate_email(email)
    validate_password(password)
    if billing_store.get_user_by_email(email):
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    user = billing_store.create_user(email, hash_password(password), name)
    if profile:
        billing_store.update_profile(user["id"], validate_profile(profile))
    token = billing_store.create_session(
        user["id"], request.headers.get("user-agent"), SESSION_TTL_DAYS
    )
    set_session_cookie(response, token)
    user = billing_store.get_user_by_id(user["id"])
    send_verification(user)
    return user


# ── Email verification ───────────────────────────────────────────────────────

def send_verification(user: dict) -> Optional[str]:
    """Issue a verification token and email it. Returns the link only when
    EMAIL_EXPOSE_LINKS is on (local development); never raises."""
    if not user or user.get("email_verified_at"):
        return None
    try:
        token = billing_store.create_auth_token(
            user["id"], "verify_email", VERIFY_TOKEN_TTL_MINUTES
        )
        emailer.send_verification(user["email"], token)
        if emailer.EXPOSE_LINKS:
            return f"/verify-email?token={token}"
    except Exception as exc:  # a mail outage must not block signup
        LOG.warning("Could not send verification email: %s", exc.__class__.__name__)
    return None


def verify_email(token: str) -> dict:
    user_id = billing_store.consume_auth_token(token, "verify_email")
    if not user_id:
        raise HTTPException(
            status_code=400,
            detail={"message": "This confirmation link is invalid or has expired.",
                    "code": "invalid_token"},
        )
    billing_store.mark_email_verified(user_id)
    return billing_store.get_user_by_id(user_id) or {}


# ── Forgot / reset password ──────────────────────────────────────────────────

def request_password_reset(email: str) -> Optional[str]:
    """Always succeeds from the caller's point of view: an unknown address gets
    the same answer as a known one, so the endpoint cannot be used to discover
    which addresses have accounts."""
    try:
        user = billing_store.get_user_by_email(validate_email(email))
    except HTTPException:
        return None
    if not user:
        return None
    if billing_store.count_recent_tokens(
            user["id"], "reset_password", 60) >= RESET_REQUESTS_PER_HOUR:
        # Throttled: stay silent rather than telling a stranger the address exists.
        LOG.info("Password reset throttled for user %s", user["id"])
        return None
    token = billing_store.create_auth_token(
        user["id"], "reset_password", RESET_TOKEN_TTL_MINUTES
    )
    emailer.send_password_reset(user["email"], token)
    if emailer.EXPOSE_LINKS:
        return f"/reset-password?token={token}"
    return None


def reset_password(token: str, new_password: str) -> None:
    validate_password(new_password)
    user_id = billing_store.consume_auth_token(token, "reset_password")
    if not user_id:
        raise HTTPException(
            status_code=400,
            detail={"message": "This reset link is invalid or has expired.",
                    "code": "invalid_token"},
        )
    billing_store.set_password(user_id, hash_password(new_password))
    # Every existing session dies with the old credential.
    billing_store.delete_all_sessions(user_id)


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