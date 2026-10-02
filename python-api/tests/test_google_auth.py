#!/usr/bin/env python3
"""
Tests for "Sign in with Google".

Run:  cd python-api && python3 -m pytest tests/test_google_auth.py -q

No network: Google is stubbed by serving a locally generated RSA public key as
the JWKS, and credentials are minted with that key. Covers token verification
(audience, issuer, expiry, signature, verified email), the account-linking
rules, and the HTTP endpoint.
"""

import datetime as dt
import itertools
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

_TEST_DIR = tempfile.mkdtemp(prefix="scigraph_google_test_")
os.environ["BILLING_DB"] = str(Path(_TEST_DIR) / "billing.db")
os.environ["SEARCH_DB"] = str(Path(_TEST_DIR) / "search.db")
os.environ["EXPORTS_DIR"] = str(Path(_TEST_DIR) / "exports")
os.environ["WORKSPACE_DIR"] = str(Path(_TEST_DIR) / "workspace")
os.environ["RAZORPAY_KEY_ID"] = "rzp_test_key_id"
os.environ["RAZORPAY_KEY_SECRET"] = "rzp_test_key_secret"
os.environ["RAZORPAY_WEBHOOK_SECRET"] = "rzp_test_webhook_secret"
os.environ["COOKIE_SECURE"] = "0"
os.environ["GOOGLE_CLIENT_ID"] = "1234567890-test.apps.googleusercontent.com"
os.environ.pop("REQUIRE_AUTH", None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import auth  # noqa: E402
import billing_store  # noqa: E402
import google_auth  # noqa: E402
import jwt  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app as engine_app  # noqa: E402

CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"]
# The module reads this at import time and another test module may already have
# imported it, so set it explicitly for this suite.
google_auth.GOOGLE_CLIENT_ID = CLIENT_ID
google_auth.GOOGLE_ENABLED = bool(CLIENT_ID)
PASSWORD = "correct-horse-battery"
_counter = itertools.count()


def unique_email(prefix="google") -> str:
    return f"{prefix}-{next(_counter)}@example.com"


def _b64(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


class GoogleKeyFixture:
    """One RSA key pair standing in for Google's published signing keys."""

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()
    kid = "test-key-1"

    @classmethod
    def jwk(cls) -> dict:
        numbers = cls.public_key.public_numbers()
        return {
            "kty": "RSA",
            "alg": "RS256",
            "use": "sig",
            "kid": cls.kid,
            "n": _b64(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, "big")),
            "e": _b64(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, "big")),
        }


def mint(sub=None, email="person@example.com", aud=CLIENT_ID,
         iss="https://accounts.google.com", email_verified=True,
         expired=False, key=None, kid=GoogleKeyFixture.kid):
    now = dt.datetime.now(dt.timezone.utc)
    claims = {
        "iss": iss,
        "aud": aud,
        # Google subjects are unique per account; mirror that so one test's
        # token cannot be mistaken for another's.
        "sub": sub if sub is not None else f"sub-for-{email}",
        "email": email,
        "email_verified": email_verified,
        "name": "Test Person",
        "iat": int((now - dt.timedelta(minutes=1)).timestamp()),
        "exp": int((now - dt.timedelta(hours=1) if expired
                    else now + dt.timedelta(hours=1)).timestamp()),
    }
    return jwt.encode(claims, (key or GoogleKeyFixture.private_key),
                      algorithm="RS256", headers={"kid": kid})


class TokenVerificationTests(unittest.TestCase):
    def setUp(self):
        self._keys = google_auth.signing_keys
        # Serve our own public key as Google's JWKS.
        google_auth._cached_keys = {GoogleKeyFixture.kid: GoogleKeyFixture.public_key}
        google_auth._cached_at = time.time()
        self.addCleanup(setattr, google_auth, "signing_keys", self._keys)

    def test_valid_token_returns_claims(self):
        claims = google_auth.verify_id_token(mint())
        self.assertEqual(claims["email"], "person@example.com")
        self.assertEqual(claims["sub"], "sub-for-person@example.com")

    def test_token_for_another_audience_is_refused(self):
        with self.assertRaises(google_auth.GoogleAuthError) as ctx:
            google_auth.verify_id_token(mint(aud="someone-else.apps.googleusercontent.com"))
        self.assertEqual(ctx.exception.status, 401)

    def test_token_from_another_issuer_is_refused(self):
        with self.assertRaises(google_auth.GoogleAuthError):
            google_auth.verify_id_token(mint(iss="https://evil.example.com"))

    def test_expired_token_is_refused(self):
        with self.assertRaises(google_auth.GoogleAuthError):
            google_auth.verify_id_token(mint(expired=True))

    def test_token_signed_with_another_key_is_refused(self):
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with self.assertRaises(google_auth.GoogleAuthError):
            google_auth.verify_id_token(mint(key=other_key))

    def test_token_from_an_unknown_key_id_is_refused(self):
        with self.assertRaises(google_auth.GoogleAuthError):
            google_auth.verify_id_token(mint(kid="unknown-key"))

    def test_unverified_email_is_refused(self):
        with self.assertRaises(google_auth.GoogleAuthError):
            google_auth.verify_id_token(mint(email_verified=False))

    def test_string_email_verified_is_accepted(self):
        # Google sends a boolean, but tolerate the string form.
        token = jwt.encode(
            {"iss": "https://accounts.google.com", "aud": CLIENT_ID, "sub": "s",
             "email": "p@example.com", "email_verified": "true",
             "iat": int(time.time()) - 60, "exp": int(time.time()) + 3600},
            GoogleKeyFixture.private_key, algorithm="RS256",
            headers={"kid": GoogleKeyFixture.kid})
        self.assertEqual(google_auth.verify_id_token(token)["sub"], "s")

    def test_garbage_is_refused(self):
        for bad in ("", "not-a-jwt", "a.b.c", "x" * (google_auth.MAX_CREDENTIAL_LEN + 1)):
            with self.assertRaises(google_auth.GoogleAuthError):
                google_auth.verify_id_token(bad)

    def test_missing_client_id_disables_google_sign_in(self):
        with self.assertRaises(google_auth.GoogleAuthError) as ctx:
            google_auth.verify_id_token(mint(), client_id="")
        self.assertEqual(ctx.exception.status, 503)


class GoogleAccountTests(unittest.TestCase):
    def setUp(self):
        google_auth._cached_keys = {GoogleKeyFixture.kid: GoogleKeyFixture.public_key}
        google_auth._cached_at = time.time()
        self.client = TestClient(engine_app.app)

    def test_first_google_sign_in_creates_an_account(self):
        email = unique_email()
        res = self.client.post("/api/account/google",
                               json={"credential": mint(email=email)})
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["user"]["email"], email)
        self.assertTrue(body["user"]["email_verified"],
                        "Google already verified the address")
        self.assertEqual(body["entitlement"]["plan"], "free")

    def test_the_new_account_can_search_and_uses_the_same_quota(self):
        email = unique_email()
        self.client.post("/api/account/google", json={"credential": mint(email=email)})
        me = self.client.get("/api/account/me").json()
        self.assertTrue(me["entitlement"]["signed_in"])
        self.assertEqual(me["entitlement"]["searches_limit"], 3)

    def test_signing_in_again_reuses_the_same_account(self):
        email = unique_email()
        first = self.client.post("/api/account/google",
                                 json={"credential": mint(sub="s-a", email=email)})
        self.client.post("/api/account/logout")
        second = self.client.post("/api/account/google",
                                  json={"credential": mint(sub="s-a", email=email)})
        self.assertEqual(first.json()["user"]["id"], second.json()["user"]["id"])
        self.assertEqual(billing_store.get_user_by_email(email)["google_sub"], "s-a")

    def test_a_different_google_identity_does_not_reuse_the_account(self):
        first_email, second_email = unique_email(), unique_email()
        first = self.client.post("/api/account/google",
                                 json={"credential": mint(sub="s-1", email=first_email)})
        second = self.client.post("/api/account/google",
                                  json={"credential": mint(sub="s-2", email=second_email)})
        self.assertNotEqual(first.json()["user"]["id"], second.json()["user"]["id"])
        self.assertEqual(second.json()["user"]["email"], second_email)

    def test_a_password_account_is_never_taken_over_by_google(self):
        email = unique_email("passworded")
        billing_store.create_user(email, auth.hash_password(PASSWORD))
        res = self.client.post("/api/account/google",
                               json={"credential": mint(email=email)})
        self.assertEqual(res.status_code, 400, res.text)
        self.assertEqual(res.json()["detail"]["code"], "password_account_exists")
        # The password must still work, and no session was handed out.
        self.assertEqual(
            self.client.post("/api/account/login",
                             json={"email": email, "password": PASSWORD}).status_code, 200)

    def test_a_google_account_cannot_be_signed_into_with_a_password(self):
        email = unique_email()
        self.client.post("/api/account/google", json={"credential": mint(email=email)})
        user = billing_store.get_user_by_email(email)
        self.assertTrue(billing_store.is_oauth_account(user))
        for guess in ("", "oauth:" + user["google_sub"], "password", user["google_sub"]):
            self.assertFalse(auth.verify_password(guess, user["password_hash"]))

    def test_invalid_credential_is_refused_with_a_reason(self):
        res = self.client.post("/api/account/google", json={"credential": "garbage"})
        self.assertIn(res.status_code, (400, 401))
        self.assertIsNone(self.client.get("/api/account/me").json()["user"])

    def test_google_link_survives_a_password_reset_request(self):
        # A Google-only account has no password to reset; the request must not
        # crash or send anything.
        email = unique_email()
        self.client.post("/api/account/google", json={"credential": mint(email=email)})
        self.assertEqual(
            self.client.post("/api/account/forgot-password", json={"email": email}).status_code,
            200)

    def test_jwks_is_served_from_google_and_cached(self):
        original = google_auth.requests.get
        calls = []

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {"keys": [GoogleKeyFixture.jwk()]}

        def fake_get(url, timeout=None):
            calls.append(url)
            return FakeResponse()

        google_auth.requests.get = fake_get
        google_auth._cached_keys = {}
        google_auth._cached_at = 0.0
        try:
            keys = google_auth.signing_keys()
            self.assertIn(GoogleKeyFixture.kid, keys)
            google_auth.signing_keys()
            self.assertEqual(len(calls), 1, "keys must be cached, not refetched")
            self.assertEqual(calls[0], google_auth.CERTS_URL)
        finally:
            google_auth.requests.get = original
            google_auth._cached_keys = {}
            google_auth._cached_at = 0.0

    def test_a_rotated_key_is_refetched_once(self):
        original = google_auth.signing_keys
        seen = []

        def rotating(force_refresh=False):
            seen.append(force_refresh)
            if force_refresh:
                return {GoogleKeyFixture.kid: GoogleKeyFixture.public_key}
            return {}

        google_auth.signing_keys = rotating
        try:
            self.client.post("/api/account/google",
                             json={"credential": mint(email=unique_email("rotated"))})
            self.assertIn(True, seen, "an unknown kid must trigger a refresh")
        finally:
            google_auth.signing_keys = original


if __name__ == "__main__":
    unittest.main(verbosity=2)
