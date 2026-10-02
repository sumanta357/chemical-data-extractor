"""
Shared test helpers.

Identity comes from Supabase, so tests do not create passwords or cookies:
they present a bearer token and stub what Supabase would answer for it.
"""

import unittest
import uuid

import auth
import billing_store
import supabase_auth

# A fixed, well-formed UUID so assertions stay readable.
TEST_SUB = "11111111-2222-4333-8444-555555555555"


def identity(sub=TEST_SUB, email="user@example.com", verified=True, name="Test User"):
    return {"id": sub, "email": email, "email_verified": verified,
            "name": name, "provider": "email"}


class SupabaseStub:
    """Makes `verify_access_token` return a chosen identity, no network."""

    def __init__(self, sub=None, email=None, verified=True, name="Test User"):
        # A fresh subject *and* address per stub unless one is given, so two
        # stubs are two different Supabase users — as Supabase would treat
        # them. Tests that model the same user pass the same `sub`.
        self.identity = identity(sub or str(uuid.uuid4()),
                                 email or f"user-{uuid.uuid4().hex[:8]}@example.com",
                                 verified, name)
        self.calls = 0

    def install(self, testcase: unittest.TestCase):
        original = supabase_auth.verify_access_token

        def verify(token):
            self.calls += 1
            if token != "test-access-token":
                raise supabase_auth.SupabaseAuthError("bad token", 401)
            return dict(self.identity)

        supabase_auth.verify_access_token = verify
        testcase.addCleanup(lambda: setattr(supabase_auth, "verify_access_token", original))
        # The server must be marked configured for identity to resolve.
        original_enabled = supabase_auth.SUPABASE_ENABLED
        supabase_auth.SUPABASE_ENABLED = True
        testcase.addCleanup(
            lambda: setattr(supabase_auth, "SUPABASE_ENABLED", original_enabled))
        return self

    def account(self):
        """The billing account this identity maps to (created on demand)."""
        return auth.resolve_account(dict(self.identity))


AUTH_HEADERS = {"Authorization": "Bearer test-access-token"}


def make_account(email: str, plan: str = "free", sub: str = None, **profile) -> dict:
    """A billing account the way the app now creates them: linked to Supabase,
    with no password of its own."""
    sub = sub or str(uuid.uuid4())
    account = billing_store.create_supabase_user(
        sub, email, profile.pop("name", None), bool(profile.pop("verified", False)))
    if plan != "free":
        account = billing_store.set_plan(account["id"], plan)
    if profile:
        account = billing_store.update_profile(account["id"], profile)
    return billing_store.get_user_by_id(account["id"])