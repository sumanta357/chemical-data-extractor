#!/usr/bin/env python3
"""
"Sign in with Google" — verification of Google's ID token (credential).

The browser gets a credential from Google Identity Services and posts it here.
Nothing about the user is trusted: the JWT is verified against Google's
published signing keys (RS256) and must carry *our* client id as its audience,
one of Google's issuers and an unexpired timestamp. Only then is an email
address read out of it.

Signing keys are cached for an hour (Google rotates them rarely) so a login
does not make two HTTPS calls every time.

Env
---
GOOGLE_CLIENT_ID  the OAuth 2.0 client id of the web app. Unset => Google
                  sign-in is disabled and the endpoint reports 503.
"""

import logging
import os
import threading
import time
from typing import Optional

import jwt
import requests

LOG = logging.getLogger("scigraph.google")

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("accounts.google.com", "https://accounts.google.com")
CERTS_TTL_SECONDS = 3600
HTTP_TIMEOUT = 10
MAX_CREDENTIAL_LEN = 8192

GOOGLE_ENABLED = bool(GOOGLE_CLIENT_ID)

_lock = threading.Lock()
_cached_keys: dict = {}
_cached_at: float = 0.0


class GoogleAuthError(Exception):
    """Verification failed. `status` is the HTTP code the API should answer."""

    def __init__(self, message: str, status: int = 401):
        super().__init__(message)
        self.status = status


def _email_verified(value) -> bool:
    """Google sends a JSON boolean, but tolerate the string form."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


def signing_keys(force_refresh: bool = False) -> dict:
    """Google's public signing keys, keyed by `kid`, cached for an hour."""
    global _cached_keys, _cached_at
    with _lock:
        fresh = _cached_keys and (time.time() - _cached_at) < CERTS_TTL_SECONDS
        if fresh and not force_refresh:
            return _cached_keys
        try:
            resp = requests.get(CERTS_URL, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            jwks = resp.json()
        except (requests.RequestException, ValueError) as exc:
            if _cached_keys:
                # A refresh failure must not lock everyone out.
                LOG.warning("Google key refresh failed (%s); using cached keys",
                            exc.__class__.__name__)
                _cached_at = time.time()
                return _cached_keys
            raise GoogleAuthError("Could not reach Google to verify this sign-in", 502)

        keys = {}
        for jwk in jwks.get("keys", []):
            if jwk.get("alg") != "RS256" or not jwk.get("kid"):
                continue
            try:
                keys[jwk["kid"]] = jwt.PyJWK.from_dict(jwk).key
            except Exception as exc:  # a single bad key must not break the rest
                LOG.warning("Skipping unusable Google key: %s", exc.__class__.__name__)
        if not keys:
            raise GoogleAuthError("Google returned no usable signing keys", 502)
        _cached_keys = keys
        _cached_at = time.time()
        return keys


def verify_id_token(credential: str, client_id: Optional[str] = None) -> dict:
    """Verify a Google credential and return its claims.

    Raises GoogleAuthError for anything that is not a valid, unexpired token
    minted by Google for this application."""
    # An explicit empty client_id means "not configured" — falling back to the
    # environment here would let a caller skip the audience check.
    expected_client_id = (GOOGLE_CLIENT_ID if client_id is None else client_id).strip()
    if not expected_client_id:
        raise GoogleAuthError("Google sign-in is not configured on this server", 503)
    if not credential or len(credential) > MAX_CREDENTIAL_LEN:
        raise GoogleAuthError("Missing Google credential", 400)

    try:
        # Read the header/claims unverified only to pick the key; everything
        # that is trusted is re-checked below with the signature verified.
        unverified = jwt.decode(credential, options={"verify_signature": False,
                                                     "verify_aud": False,
                                                     "verify_exp": False})
    except jwt.PyJWTError:
        raise GoogleAuthError("That Google sign-in could not be read", 400)

    kid = jwt.get_unverified_header(credential).get("kid")
    keys = signing_keys()
    key = keys.get(kid)
    if key is None:
        keys = signing_keys(force_refresh=True)  # Google rotated the key
        key = keys.get(kid)
    if key is None:
        raise GoogleAuthError("That Google sign-in was signed with an unknown key", 401)

    try:
        claims = jwt.decode(
            credential,
            key=key,
            algorithms=["RS256"],
            audience=expected_client_id,
            issuer=list(ISSUERS),
            options={"require": ["exp", "iat", "aud", "iss", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        raise GoogleAuthError("That Google sign-in has expired. Please try again.", 401)
    except jwt.PyJWTError:
        raise GoogleAuthError("Google could not verify this sign-in", 401)

    if not _email_verified(claims.get("email_verified")):
        raise GoogleAuthError("That Google account has no verified email address", 401)
    if not claims.get("email") or not claims.get("sub"):
        raise GoogleAuthError("That Google account is missing an email address", 401)
    return claims
