#!/usr/bin/env python3
"""
Tests for Supabase-backed identity.

Run:  cd python-api && python3 -m pytest tests/test_supabase_auth.py -q

No network: Supabase is stubbed at the HTTP boundary. Covers token
verification (including the failure modes), and how a verified Supabase user
maps onto a billing account.
"""

import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

_TEST_DIR = tempfile.mkdtemp(prefix="scigraph_supabase_test_")
os.environ["BILLING_DB"] = str(Path(_TEST_DIR) / "billing.db")
os.environ["SEARCH_DB"] = str(Path(_TEST_DIR) / "search.db")
os.environ["EXPORTS_DIR"] = str(Path(_TEST_DIR) / "exports")
os.environ["WORKSPACE_DIR"] = str(Path(_TEST_DIR) / "workspace")
os.environ["RAZORPAY_KEY_ID"] = "rzp_test_key_id"
os.environ["RAZORPAY_KEY_SECRET"] = "rzp_test_key_secret"
os.environ["RAZORPAY_WEBHOOK_SECRET"] = "rzp_test_webhook_secret"
os.environ["SUPABASE_URL"] = "https://project.supabase.co"
os.environ["SUPABASE_ANON_KEY"] = "test-anon-key"
os.environ.pop("REQUIRE_AUTH", None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import auth  # noqa: E402
import billing_store  # noqa: E402
import supabase_auth  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app as engine_app  # noqa: E402
from support import AUTH_HEADERS, SupabaseStub  # noqa: E402

# Another test module may have imported these first, without the env set.
supabase_auth.SUPABASE_URL = os.environ["SUPABASE_URL"]
supabase_auth.SUPABASE_ANON_KEY = os.environ["SUPABASE_ANON_KEY"]
supabase_auth.SUPABASE_ENABLED = True

PASSWORD = "correct-horse-battery"


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = ""

    def json(self):
        return self._payload


class SupabaseUserPayload:
    def __init__(self, sub=None, email="person@example.com", confirmed=True,
                 name="Test Person", provider="email"):
        self.sub = sub or str(uuid.uuid4())
        self.email = email
        self.confirmed = confirmed
        self.name = name
        self.provider = provider
        self.captured = {}

    def response(self):
        return FakeResponse(200, {
            "id": self.sub,
            "email": self.email,
            "email_confirmed_at": "2026-01-01T00:00:00Z" if self.confirmed else None,
            "confirmed_at": "2026-01-01T00:00:00Z" if self.confirmed else None,
            "user_metadata": {"full_name": self.name},
            "app_metadata": {"provider": self.provider},
        })


class TokenVerificationTests(unittest.TestCase):
    def setUp(self):
        self._get = supabase_auth.requests.get
        self.addCleanup(lambda: setattr(supabase_auth.requests, "get", self._get))

    def _serve(self, response):
        calls = {}

        def fake_get(url, headers=None, timeout=None):
            calls["url"] = url
            calls["headers"] = headers or {}
            return response

        supabase_auth.requests.get = fake_get
        return calls

    def test_valid_token_returns_a_normalised_identity(self):
        payload = SupabaseUserPayload(sub="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
        calls = self._serve(payload.response())
        identity = supabase_auth.verify_access_token("jwt-token")

        self.assertEqual(identity["id"], payload.sub)
        self.assertEqual(identity["email"], "person@example.com")
        self.assertTrue(identity["email_verified"])
        self.assertEqual(identity["name"], "Test Person")
        self.assertTrue(calls["url"].endswith("/auth/v1/user"))
        self.assertEqual(calls["headers"]["Authorization"], "Bearer jwt-token")
        self.assertEqual(calls["headers"]["apikey"], supabase_auth.SUPABASE_ANON_KEY)

    def test_unconfirmed_email_is_reported_as_unverified(self):
        self._serve(SupabaseUserPayload(confirmed=False).response())
        self.assertFalse(supabase_auth.verify_access_token("jwt")["email_verified"])

    def test_provider_is_reported(self):
        self._serve(SupabaseUserPayload(provider="google").response())
        self.assertEqual(supabase_auth.verify_access_token("jwt")["provider"], "google")

    def test_rejected_token_raises_401(self):
        self._serve(FakeResponse(401, {"message": "invalid"}))
        with self.assertRaises(supabase_auth.SupabaseAuthError) as ctx:
            supabase_auth.verify_access_token("jwt")
        self.assertEqual(ctx.exception.status, 401)

    def test_supabase_outage_raises_502_not_401(self):
        def boom(*args, **kwargs):
            raise supabase_auth.requests.RequestException("timeout")

        supabase_auth.requests.get = boom
        with self.assertRaises(supabase_auth.SupabaseAuthError) as ctx:
            supabase_auth.verify_access_token("jwt")
        self.assertEqual(ctx.exception.status, 502)

    def test_incomplete_payload_is_refused(self):
        self._serve(FakeResponse(200, {"id": "not-a-uuid", "email": "a@b.co"}))
        with self.assertRaises(supabase_auth.SupabaseAuthError):
            supabase_auth.verify_access_token("jwt")
        self._serve(FakeResponse(200, {"id": str(uuid.uuid4())}))
        with self.assertRaises(supabase_auth.SupabaseAuthError):
            supabase_auth.verify_access_token("jwt")

    def test_missing_token_is_refused_without_a_network_call(self):
        calls = self._serve(FakeResponse(200, {}))
        for bad in ("", None, "x" * (supabase_auth.MAX_TOKEN_LEN + 1)):
            with self.assertRaises(supabase_auth.SupabaseAuthError):
                supabase_auth.verify_access_token(bad)
        self.assertEqual(calls, {})

    def test_unconfigured_server_refuses_to_authenticate(self):
        self._serve(FakeResponse(200, {}))
        original = supabase_auth.SUPABASE_ENABLED
        supabase_auth.SUPABASE_ENABLED = False
        try:
            with self.assertRaises(supabase_auth.SupabaseAuthError) as ctx:
                supabase_auth.verify_access_token("jwt")
            self.assertEqual(ctx.exception.status, 503)
        finally:
            supabase_auth.SUPABASE_ENABLED = original


class IdentityMappingTests(unittest.TestCase):
    def setUp(self):
        self.stub = SupabaseStub().install(self)

    def test_first_request_creates_the_billing_account(self):
        account = self.stub.account()
        self.assertEqual(account["email"], self.stub.identity["email"])
        self.assertEqual(account["supabase_user_id"], self.stub.identity["id"])
        self.assertEqual(account["plan"], "free")

    def test_later_requests_reuse_the_same_account(self):
        first = self.stub.account()
        second = self.stub.account()
        self.assertEqual(first["id"], second["id"])

    def test_a_confirmed_email_marks_the_account_verified(self):
        self.assertTrue(self.stub.account()["email_verified_at"])
        unconfirmed = SupabaseStub(email="fresh@example.com", verified=False).install(self)
        self.assertIsNone(unconfirmed.account()["email_verified_at"])

    def test_name_and_email_changes_are_mirrored(self):
        account = self.stub.account()
        renamed = SupabaseStub(sub=self.stub.identity["id"],
                               email="renamed@example.com", name="New Name")
        renamed.install(self)
        updated = renamed.account()
        self.assertEqual(updated["id"], account["id"])
        self.assertEqual(updated["email"], "renamed@example.com")
        self.assertEqual(updated["name"], "New Name")

    def test_lookup_is_by_supabase_id_even_after_the_email_changes(self):
        stub = SupabaseStub(email="original@example.com")
        stub.install(self)
        account = stub.account()
        # The same Supabase user, now with a different address.
        moved = SupabaseStub(sub=stub.identity["id"], email="moved@example.com")
        moved.install(self)
        self.assertEqual(moved.account()["id"], account["id"])

    def test_an_address_already_in_use_does_not_break_sign_in(self):
        # Supabase keeps addresses unique, so this cannot arise through the
        # normal flow; if it somehow does, fall back instead of failing.
        first = SupabaseStub(email="shared@example.com")
        first.install(self)
        first_account = first.account()
        second = SupabaseStub(email="shared@example.com")
        second.install(self)
        self.assertEqual(second.account()["id"], first_account["id"])


class AuthenticatedApiTests(unittest.TestCase):
    def setUp(self):
        self.stub = SupabaseStub().install(self)
        self.client = TestClient(engine_app.app)

    def test_a_bearer_token_reveals_the_account(self):
        res = self.client.get("/api/account/me", headers=AUTH_HEADERS)
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["user"]["email"], self.stub.identity["email"])
        self.assertTrue(body["entitlement"]["signed_in"])
        self.assertTrue(body["user"]["email_verified"])

    def test_no_token_stays_anonymous(self):
        body = self.client.get("/api/account/me").json()
        self.assertIsNone(body["user"])
        self.assertFalse(body["entitlement"]["signed_in"])
        self.assertEqual(body["entitlement"]["status"], "anonymous")

    def test_a_bad_token_is_treated_as_anonymous_not_an_error(self):
        body = self.client.get("/api/account/me",
                               headers={"Authorization": "Bearer nope"}).json()
        self.assertIsNone(body["user"])
        self.assertFalse(body["entitlement"]["signed_in"])

    def test_protected_endpoints_require_a_session(self):
        self.assertEqual(self.client.get("/api/account/profile").status_code, 401)
        self.assertEqual(self.client.get("/api/account/usage").status_code, 401)
        self.assertEqual(
            self.client.get("/api/account/profile", headers=AUTH_HEADERS).status_code, 200)

    def test_a_search_is_attributed_to_the_session_account(self):
        account = self.stub.account()
        original = engine_app.run_search_in_background

        async def noop(*args, **kwargs):
            return None

        engine_app.run_search_in_background = noop
        try:
            res = self.client.post("/api/search",
                                   json={"query": "aspirin", "hops": 1},
                                   headers=AUTH_HEADERS)
        finally:
            engine_app.run_search_in_background = original
        self.assertEqual(res.status_code, 200, res.text)
        search_id = res.json()["search_id"]

        # Completing the job credits exactly this account.
        engine_app._record_search_usage(search_id)
        self.assertEqual(billing_store.get_user_by_id(account["id"])["searches_used"], 1)
        ent = self.client.get("/api/account/me", headers=AUTH_HEADERS).json()["entitlement"]
        self.assertEqual(ent["searches_used"], 1)
        self.assertEqual(ent["searches_limit"], 3)

    def test_the_billing_config_exposes_the_supabase_project(self):
        body = self.client.get("/api/billing/config").json()
        self.assertTrue(body["supabase"]["configured"])
        self.assertEqual(body["supabase"]["url"], supabase_auth.SUPABASE_URL)
        # The anon key is publishable by design, but never a service_role key.
        self.assertNotIn("service_role", str(body["supabase"]))

    def test_sign_in_and_sign_up_endpoints_are_gone(self):
        """Authentication is Supabase's; the app must not re-expose it."""
        for path, payload in (
            ("/api/account/login", {"email": "a@b.co", "password": PASSWORD}),
            ("/api/account/register", {"email": "a@b.co", "password": PASSWORD}),
            ("/api/account/logout", {}),
            ("/api/account/forgot-password", {"email": "a@b.co"}),
            ("/api/account/reset-password", {"token": "t", "password": PASSWORD}),
            ("/api/account/google", {"credential": "c"}),
        ):
            res = self.client.post(path, json=payload)
            self.assertIn(res.status_code, (404, 405), f"{path} must not exist")

    def test_no_session_cookie_is_ever_set(self):
        res = self.client.get("/api/account/me", headers=AUTH_HEADERS)
        self.assertNotIn("set-cookie", {k.lower() for k in res.headers})
        self.assertNotIn(auth.COOKIE_NAME if hasattr(auth, "COOKIE_NAME") else "scigraph_session",
                         "".join(res.headers.keys()))


if __name__ == "__main__":
    unittest.main(verbosity=2)