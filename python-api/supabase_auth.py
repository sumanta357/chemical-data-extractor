#!/usr/bin/env python3
"""
Identity comes from Supabase Auth; billing data stays in our own database.

The browser holds a Supabase access token and sends it as
`Authorization: Bearer <token>`. We never mint sessions ourselves and never see
a password: the token is checked against Supabase, which is the only authority
on who someone is.

Verification calls Supabase's own `GET /auth/v1/user` endpoint — the supported
way to turn a token into a user, and the one that keeps working whichever
signing keys the project uses (HS256 secret or the newer asymmetric JWKS).

Env
---
SUPABASE_URL        project URL, e.g. https://abcdefgh.supabase.co
SUPABASE_ANON_KEY   the *anon / publishable* key. It is public by design and is
                    the same value the browser uses. Never the service_role key.
"""

import logging
import os
from typing import Optional

import requests

LOG = logging.getLogger("scigraph.supabase")

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "").strip()

SUPABASE_ENABLED = bool(SUPABASE_URL and SUPABASE_ANON_KEY)
HTTP_TIMEOUT = 10
MAX_TOKEN_LEN = 8192


class SupabaseAuthError(Exception):
    """The token could not be verified. `status` is the code to answer with."""

    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


def is_uuid(value: str) -> bool:
    """A Supabase user id is a UUID; anything else is not ours to trust."""
    if not value or len(value) > 36:
        return False
    try:
        import uuid
        uuid.UUID(str(value))
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _display_name(payload: dict) -> Optional[str]:
    metadata = payload.get("user_metadata") or {}
    for key in ("full_name", "name", "display_name"):
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:120]
    return None


def verify_access_token(token: str) -> dict:
    """Exchange an access token for the Supabase user behind it.

    Returns a normalised identity: id, email, email_verified, name, provider.
    Raises SupabaseAuthError for an unconfigured server, a missing token, or a
    token Supabase does not accept.
    """
    if not SUPABASE_ENABLED:
        raise SupabaseAuthError("Authentication is not configured on this server", 503)
    if not token or len(token) > MAX_TOKEN_LEN:
        raise SupabaseAuthError("Missing session token", 401)

    try:
        resp = requests.get(
            f"{SUPABASE_URL}/auth/v1/user",
            headers={"apikey": SUPABASE_ANON_KEY,
                     "Authorization": f"Bearer {token}"},
            timeout=HTTP_TIMEOUT,
        )
    except requests.RequestException as exc:
        LOG.warning("Could not reach Supabase: %s", exc.__class__.__name__)
        raise SupabaseAuthError("Could not verify your session. Please try again.", 502)

    if resp.status_code in (401, 403):
        raise SupabaseAuthError("Your session has expired. Please sign in again.", 401)
    if resp.status_code >= 400:
        LOG.warning("Supabase user lookup returned HTTP %s", resp.status_code)
        raise SupabaseAuthError("Could not verify your session. Please try again.", 502)

    try:
        payload = resp.json()
    except ValueError:
        raise SupabaseAuthError("Supabase returned an unreadable response", 502)

    user_id = str(payload.get("id") or "")
    email = (payload.get("email") or "").strip().lower()
    if not is_uuid(user_id) or not email:
        raise SupabaseAuthError("Supabase returned an incomplete user", 502)

    app_metadata = payload.get("app_metadata") or {}
    return {
        "id": user_id,
        "email": email,
        # Supabase reports a confirmed address as `email_confirmed_at`; some
        # responses only carry `confirmed_at`.
        "email_verified": bool(payload.get("email_confirmed_at")
                               or payload.get("confirmed_at")),
        "name": _display_name(payload),
        "provider": (app_metadata.get("provider") or "email"),
    }


def bearer_token(request) -> Optional[str]:
    """The access token from the Authorization header."""
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return None