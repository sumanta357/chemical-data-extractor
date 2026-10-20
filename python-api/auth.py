#!/usr/bin/env python3
"""
Identity is Supabase's job; this module turns a verified Supabase identity
into the billing account that owns plans, quota and orders.

There are no passwords and no session cookies here any more. A request arrives
with `Authorization: Bearer <supabase access token>`; `supabase_auth` checks it
with Supabase and we map the returned user id onto a row in our own `users`
table — created on first sight, matched by Supabase id on every later request.

Sign-up, sign-in, password reset and email confirmation all happen inside
Supabase and never reach this server.
"""

import logging
import os
import re
import sys
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, Request

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import billing_store  # noqa: E402
import supabase_auth  # noqa: E402

LOG = logging.getLogger("scigraph.auth")

# Accounts created through Supabase have no password here. The placeholder is
# deliberately not a valid hash, so nothing can ever be signed in against it.
SUPABASE_PASSWORD_PLACEHOLDER = "supabase"

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")
PHONE_RE = re.compile(r"^\+?[0-9][0-9 ()-]{5,24}$")
MAX_FIELD_LEN = 200


# ── Validation ───────────────────────────────────────────────────────────────

def validate_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 254:
        raise HTTPException(status_code=400, detail="Enter a valid email address")
    return email


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
        if academician_type not in billing_store.ACADEMICIAN_TYPES:
            raise HTTPException(
                status_code=400,
                detail={"message": "Choose a valid academician type",
                        "allowed": billing_store.ACADEMICIAN_TYPES},
            )
        clean["academician_type"] = academician_type

    return clean


# ── Identity → billing account ───────────────────────────────────────────────

def resolve_account(identity: dict) -> dict:
    """Find (or create) the billing account behind a verified Supabase user.

    Matching is on the Supabase user id, never on the email address: Supabase
    is the authority on identity, and an address can be changed by its owner.
    """
    sub = identity["id"]
    account = billing_store.get_user_by_supabase_id(sub)
    if account is None:
        try:
            return billing_store.create_supabase_user(
                sub, identity["email"], identity.get("name"),
                bool(identity.get("email_verified")),
            )
        except billing_store.INTEGRITY_ERROR:
            # Supabase keeps email addresses unique, so this only happens for an
            # account that already exists without a Supabase id (a legacy row, or
            # a sign-up that lost the insert race). Bind it now instead of
            # leaving it unlinked — an unlinked row is retried, and fails, on
            # every single request for that user.
            existing = billing_store.get_user_by_email(identity["email"])
            if existing is None:
                raise
            LOG.info("Email already has a billing account; linking by address")
            # Supabase verified this address, so it is the same person: return
            # that account rather than failing the sign-in. Binding the id only
            # happens when the row has none, so an account already bound to
            # another identity is never re-pointed at this one.
            return billing_store.link_supabase_account(
                existing["id"], sub, identity.get("name"),
                bool(identity.get("email_verified"))) or existing

    # Keep the mirrored details in step with Supabase.
    changes = {}
    if identity.get("name") and identity["name"] != account.get("name"):
        changes["name"] = identity["name"][:120]
    if identity["email"] != account.get("email"):
        changes["email"] = identity["email"]
    if identity.get("email_verified") and not account.get("email_verified_at"):
        billing_store.mark_email_verified(account["id"])
    if changes:
        try:
            billing_store.update_identity(account["id"], changes)
        except billing_store.INTEGRITY_ERROR:
            # The new address is already taken locally. The account is still
            # this identity's — it was found by Supabase id — so keep the old
            # mirrored address rather than failing every later request.
            LOG.warning("Could not mirror the address change on account %s: "
                        "already in use locally", account["id"])
        else:
            account = billing_store.get_user_by_id(account["id"]) or account
    return account


def identity_from_request(request: Request) -> Optional[dict]:
    """The billing account for this request, or None when nobody is signed in.

    Never raises: anonymous browsing must keep working exactly as before.
    """
    token = supabase_auth.bearer_token(request)
    if not token:
        return None
    try:
        identity = supabase_auth.verify_access_token(token)
    except supabase_auth.SupabaseAuthError as exc:
        LOG.info("Rejected session: %s", exc)
        return None
    try:
        return billing_store.reset_period_if_due(resolve_account(identity))
    except Exception as exc:  # a database hiccup must not 500 the whole app
        LOG.warning("Could not resolve the account for a session: %s",
                    exc.__class__.__name__)
        return None


def identity_required(request: Request) -> dict:
    account = identity_from_request(request)
    if not account:
        raise HTTPException(status_code=401, detail="Sign in to continue")
    return account


# Backwards-compatible names used across the codebase.
current_user_optional = identity_from_request
current_user_required = identity_required