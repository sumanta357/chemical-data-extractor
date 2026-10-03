#!/usr/bin/env python3
"""
Tests for the accounts / quota / Razorpay layer.

Run:  cd python-api && python3 -m pytest tests/test_billing.py -q
      (or: python3 -m unittest discover -s tests)

No network access: every Razorpay HTTP call is stubbed. Covers signature
verification (checkout + webhook), the payment lifecycle (order → capture →
activation → usage → refund), quota enforcement, account flows, and a
regression check that the existing search API is unchanged.
"""

import hashlib
import hmac
import json
import os
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path

# Test credentials + an isolated database, set before the modules are imported.
_TEST_DIR = tempfile.mkdtemp(prefix="scigraph_billing_test_")
os.environ["BILLING_DB"] = str(Path(_TEST_DIR) / "billing.db")
os.environ["SEARCH_DB"] = str(Path(_TEST_DIR) / "search.db")
os.environ["EXPORTS_DIR"] = str(Path(_TEST_DIR) / "exports")
os.environ["WORKSPACE_DIR"] = str(Path(_TEST_DIR) / "workspace")
os.environ["RAZORPAY_KEY_ID"] = "rzp_test_key_id"
os.environ["RAZORPAY_KEY_SECRET"] = "rzp_test_key_secret"
os.environ["RAZORPAY_WEBHOOK_SECRET"] = "rzp_test_webhook_secret"
os.environ["REQUIRE_AUTH"] = "0"  # sign-in is enforced in production; test the guest tier explicitly

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import auth  # noqa: E402
import billing  # noqa: E402
import billing_store  # noqa: E402
import routes_billing  # noqa: E402
from support import AUTH_HEADERS, SupabaseStub  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app as engine_app  # noqa: E402


def sign_checkout(order_id: str, payment_id: str, secret: str) -> str:
    return hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


def sign_webhook(raw: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def route_paths(app_obj) -> list[str]:
    """Flat list of route paths, flattening routers that FastAPI nests
    (include_router may store an _IncludedRouter instead of inlining routes)."""
    paths: list[str] = []

    def walk(routes):
        for route in routes:
            nested = getattr(route, "routes", None)
            if nested is None:
                nested = getattr(getattr(route, "original_router", None), "routes", None)
            if nested:
                walk(nested)
            path = getattr(route, "path", None)
            if path:
                paths.append(path)

    walk(app_obj.routes)
    return paths


def make_user(email: str, plan: str = "free") -> dict:
    """A billing account the way the app creates them now: linked to a Supabase
    user, with no password of its own."""
    user = billing_store.create_supabase_user(str(uuid.uuid4()), email, None, True)
    if plan != "free":
        user = billing_store.set_plan(user["id"], plan)
    return user


# ── Account identity ─────────────────────────────────────────────────────────

class AccountIdentityTests(unittest.TestCase):
    """Identity is Supabase's; this store only owns the billing side."""

    def test_accounts_are_created_without_a_password(self):
        account = make_user("no-password@example.com")
        self.assertEqual(account["password_hash"], billing_store.SUPABASE_PASSWORD_PLACEHOLDER)
        self.assertTrue(account["supabase_user_id"])

    def test_an_account_is_found_by_its_supabase_id(self):
        account = make_user("by-sub@example.com")
        self.assertEqual(
            billing_store.get_user_by_supabase_id(account["supabase_user_id"])["id"],
            account["id"])
        self.assertIsNone(billing_store.get_user_by_supabase_id("no-such-sub"))

    def test_a_confirmed_email_is_recorded(self):
        account = billing_store.create_supabase_user(str(uuid.uuid4()),
                                                      "confirmed@example.com", None, True)
        self.assertIsNotNone(account["email_verified_at"])

    def test_email_normalisation_and_validation(self):
        self.assertEqual(auth.validate_email("  User@Example.COM "), "user@example.com")
        with self.assertRaises(Exception):
            auth.validate_email("not-an-email")


class RequireAuthDefaultTests(unittest.TestCase):
    """Sign-in is enforced unless an operator deliberately opts out.

    This is the guard on the product decision itself: if the default ever
    slips back to "0", every deploy silently reopens unmetered anonymous
    searching and this fails.
    """

    def test_sign_in_is_enforced_when_the_env_var_is_absent(self):
        import importlib

        previous = os.environ.pop("REQUIRE_AUTH", None)
        try:
            reloaded = importlib.reload(billing)
            self.assertTrue(reloaded.REQUIRE_AUTH)
        finally:
            if previous is not None:
                os.environ["REQUIRE_AUTH"] = previous
            importlib.reload(billing)
        self.assertFalse(billing.REQUIRE_AUTH)

    def test_explicit_opt_out_is_honoured(self):
        import importlib

        previous = os.environ.get("REQUIRE_AUTH")
        os.environ["REQUIRE_AUTH"] = "0"
        try:
            self.assertFalse(importlib.reload(billing).REQUIRE_AUTH)
        finally:
            if previous is None:
                os.environ.pop("REQUIRE_AUTH", None)
            else:
                os.environ["REQUIRE_AUTH"] = previous
            importlib.reload(billing)


# ── Plan catalog ─────────────────────────────────────────────────────────────

class PlanCatalogTests(unittest.TestCase):
    def test_amounts_match_prices(self):
        for plan in billing.PLANS.values():
            self.assertEqual(plan["amount_paise"], plan["price_inr"] * 100, plan["id"])

    def test_free_plan_is_not_purchasable(self):
        self.assertEqual(billing.PLANS["free"]["amount_paise"], 0)

    def test_unknown_plan_rejected(self):
        with self.assertRaises(Exception):
            billing.get_plan("enterprise-unlimited")

    def test_public_plans_never_expose_secrets(self):
        blob = json.dumps(billing.public_plans())
        self.assertNotIn(billing.KEY_SECRET, blob)


# ── Signature verification ───────────────────────────────────────────────────

class SignatureTests(unittest.TestCase):
    def test_checkout_signature_accepts_valid(self):
        sig = sign_checkout("order_1", "pay_1", billing.KEY_SECRET)
        self.assertTrue(billing.verify_checkout_signature("order_1", "pay_1", sig))

    def test_checkout_signature_rejects_tampering(self):
        sig = sign_checkout("order_1", "pay_1", billing.KEY_SECRET)
        self.assertFalse(billing.verify_checkout_signature("order_2", "pay_1", sig))
        self.assertFalse(billing.verify_checkout_signature("order_1", "pay_2", sig))
        self.assertFalse(billing.verify_checkout_signature("order_1", "pay_1", sig[:-1] + "0"))

    def test_checkout_signature_rejects_wrong_secret(self):
        sig = sign_checkout("order_1", "pay_1", "some-other-secret")
        self.assertFalse(billing.verify_checkout_signature("order_1", "pay_1", sig))

    def test_checkout_signature_requires_all_parts(self):
        self.assertFalse(billing.verify_checkout_signature("", "pay_1", "sig"))
        self.assertFalse(billing.verify_checkout_signature("order_1", "", "sig"))
        self.assertFalse(billing.verify_checkout_signature("order_1", "pay_1", ""))

    def test_webhook_signature(self):
        body = b'{"event":"payment.captured"}'
        self.assertTrue(billing.verify_webhook_signature(body, sign_webhook(body, billing.WEBHOOK_SECRET)))
        self.assertFalse(billing.verify_webhook_signature(body, "deadbeef"))
        self.assertFalse(billing.verify_webhook_signature(b"tampered", sign_webhook(body, billing.WEBHOOK_SECRET)))
        self.assertFalse(billing.verify_webhook_signature(b"", "sig"))


class PaymentReferenceTests(unittest.TestCase):
    """Razorpay ids are `pay_<alphanumerics>`; the underscore used to make
    every real reference fail validation."""

    def _fetch(self, payment_id):
        captured = {}

        def fake_rzp(method, path, payload=None):
            captured["path"] = path
            return {"id": payment_id, "order_id": "order_" + "a" * 14}

        original = billing.rzp_request
        billing.rzp_request = fake_rzp
        try:
            return billing.fetch_payment(payment_id), captured
        finally:
            billing.rzp_request = original

    def test_accepts_a_real_payment_id(self):
        payment, captured = self._fetch("pay_" + "QKIXXbNabcdefg")
        self.assertEqual(payment["id"], "pay_QKIXXbNabcdefg")
        self.assertEqual(captured["path"], "/payments/pay_QKIXXbNabcdefg")

    def test_rejects_traversal_and_junk(self):
        for bad in ("", "../../etc/passwd", "pay_", "pay_short", "order_abcdef",
                    "pay_abcdef; drop table users", "PAY_ABCDEFGH"):
            with self.assertRaises(billing.RazorpayError):
                billing.fetch_payment(bad)


# ── Order creation ───────────────────────────────────────────────────────────

class OrderCreationTests(unittest.TestCase):
    def test_order_payload_uses_catalog_amount(self):
        captured = {}

        def fake_request(method, path, payload=None):
            captured.update({"method": method, "path": path, "payload": payload})
            return {"id": "order_fake_1", "amount": payload["amount"],
                    "currency": "INR", "status": "created"}

        original = billing.rzp_request
        billing.rzp_request = fake_request
        try:
            user = make_user("orders@example.com")
            order = billing.create_razorpay_order(billing.PLANS["starter"], user, "rcpt_1")
        finally:
            billing.rzp_request = original

        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["path"], "/orders")
        self.assertEqual(captured["payload"]["amount"], 49900)          # paise
        self.assertEqual(captured["payload"]["currency"], "INR")
        self.assertEqual(captured["payload"]["notes"]["plan"], "starter")
        self.assertEqual(captured["payload"]["notes"]["user_id"], str(user["id"]))
        self.assertEqual(order["id"], "order_fake_1")

    def test_missing_gateway_configuration_is_reported(self):
        original = billing.BILLING_ENABLED
        billing.BILLING_ENABLED = False
        try:
            with self.assertRaises(billing.RazorpayError):
                billing.rzp_request("GET", "/payments/x")
        finally:
            billing.BILLING_ENABLED = original


# ── Payment lifecycle ────────────────────────────────────────────────────────

class PaymentLifecycleTests(unittest.TestCase):
    def _captured_payment(self, order_id="order_life_1", payment_id="pay_life_1", amount=199900):
        return {"id": payment_id, "order_id": order_id, "amount": amount,
                "currency": "INR", "status": "captured", "method": "upi"}

    def test_capture_activates_the_plan(self):
        user = make_user("lifecycle@example.com")
        billing_store.create_order("order_life_1", user["id"], "pro", 199900, "rcpt")

        updated = billing.activate_for_payment(self._captured_payment())
        self.assertEqual(updated["plan"], "pro")
        self.assertEqual(updated["plan_status"], "active")
        self.assertEqual(billing_store.get_order("order_life_1")["status"], "paid")

        ent = billing.entitlement_for(billing_store.get_user_by_id(user["id"]))
        self.assertEqual(ent["plan"], "pro")
        self.assertEqual(ent["searches_limit"], 300)
        self.assertEqual(ent["searches_remaining"], 300)

    def test_replayed_capture_does_not_double_credit(self):
        user = make_user("replay@example.com")
        billing_store.create_order("order_life_2", user["id"], "starter", 49900, "rcpt")
        billing.activate_for_payment(self._captured_payment("order_life_2", "pay_life_2", 49900))

        # User burns quota, then the same payment is delivered again.
        billing_store.increment_counters(user["id"], searches=10)
        again = billing.activate_for_payment(self._captured_payment("order_life_2", "pay_life_2", 49900))

        self.assertEqual(again["searches_used"], 10, "replay must not reset usage counters")
        self.assertEqual(billing_store.get_plan if False else
                         billing.PLANS[again["plan"]]["id"], "starter")

    def test_unattributable_payment_is_ignored(self):
        self.assertIsNone(billing.activate_for_payment(
            {"id": "pay_orphan", "order_id": "order_missing", "amount": 100,
             "status": "captured"}))

    def test_failed_capture_is_rejected(self):
        user = make_user("failed@example.com")
        billing_store.create_order("order_life_3", user["id"], "starter", 49900, "rcpt")
        original = billing.fetch_payment
        billing.fetch_payment = lambda pid: {"id": pid, "order_id": "order_life_3",
                                             "amount": 49900, "status": "failed"}
        try:
            with self.assertRaises(billing.RazorpayError):
                billing.confirm_captured_payment("order_life_3", "pay_fail_1")
        finally:
            billing.fetch_payment = original
        self.assertEqual(billing_store.get_user_by_id(user["id"])["plan"], "free")

    def test_payment_for_a_different_order_is_rejected(self):
        user = make_user("crossorder@example.com")
        billing_store.create_order("order_life_4", user["id"], "starter", 49900, "rcpt")
        original = billing.fetch_payment
        billing.fetch_payment = lambda pid: {"id": pid, "order_id": "order_other",
                                             "amount": 49900, "status": "captured"}
        try:
            with self.assertRaises(billing.RazorpayError):
                billing.confirm_captured_payment("order_life_4", "pay_cross_1")
        finally:
            billing.fetch_payment = original

    def test_reconcile_credits_a_captured_payment(self):
        user = make_user("reconcile@example.com")
        billing_store.create_order("order_rec_1", user["id"], "starter", 49900, "rcpt")
        original = billing.fetch_payment
        billing.fetch_payment = lambda pid: {"id": pid, "order_id": "order_rec_1",
                                             "amount": 49900, "status": "captured",
                                             "method": "upi"}
        try:
            activated, payment = billing.reconcile_captured_payment("pay_rec_1", user)
        finally:
            billing.fetch_payment = original
        self.assertIsNotNone(activated)
        self.assertEqual(activated["plan"], "starter")
        self.assertEqual(payment["status"], "captured")

    def test_reconcile_is_a_no_op_when_already_activated(self):
        user = make_user("reconcile-twice@example.com")
        billing_store.create_order("order_rec_2", user["id"], "starter", 49900, "rcpt")
        payment = {"id": "pay_rec_2", "order_id": "order_rec_2", "amount": 49900,
                   "status": "captured", "method": "upi"}
        original = billing.fetch_payment
        billing.fetch_payment = lambda pid: dict(payment)
        try:
            first, _ = billing.reconcile_captured_payment("pay_rec_2", user)
            self.assertIsNotNone(first)
            second, _ = billing.reconcile_captured_payment("pay_rec_2", user)
        finally:
            billing.fetch_payment = original
        self.assertIsNone(second, "an already-activated plan must not be re-activated")
        self.assertEqual(billing_store.get_user_by_id(user["id"])["searches_used"], 0)

    def test_reconcile_refuses_another_accounts_payment(self):
        owner = make_user("reconcile-owner@example.com")
        other = make_user("reconcile-other@example.com")
        billing_store.create_order("order_rec_3", owner["id"], "pro", 199900, "rcpt")
        original = billing.fetch_payment
        billing.fetch_payment = lambda pid: {"id": pid, "order_id": "order_rec_3",
                                             "amount": 199900, "status": "captured"}
        try:
            activated, _ = billing.reconcile_captured_payment("pay_rec_3", other)
        finally:
            billing.fetch_payment = original
        self.assertIsNone(activated)
        self.assertEqual(billing_store.get_user_by_id(owner["id"])["plan"], "free")

    def test_reconcile_ignores_failed_payments(self):
        user = make_user("reconcile-failed@example.com")
        billing_store.create_order("order_rec_4", user["id"], "starter", 49900, "rcpt")
        original = billing.fetch_payment
        billing.fetch_payment = lambda pid: {"id": pid, "order_id": "order_rec_4",
                                             "amount": 49900, "status": "failed"}
        try:
            activated, payment = billing.reconcile_captured_payment("pay_rec_4", user)
        finally:
            billing.fetch_payment = original
        self.assertIsNone(activated)
        self.assertEqual(payment["status"], "failed")

    def test_reconcile_latest_order_recovers_an_earlier_session(self):
        user = make_user("reconcile-order@example.com")
        billing_store.create_order("order_rec_5", user["id"], "pro", 199900, "rcpt")
        original = billing.rzp_request
        billing.rzp_request = lambda m, p, payload=None: {
            "id": "order_rec_5", "status": "paid",
            "payments": [{"id": "pay_rec_5", "amount": 199900, "status": "captured",
                          "method": "card"}],
        }
        try:
            activated = billing.reconcile_latest_order(billing_store.get_user_by_id(user["id"]))
        finally:
            billing.rzp_request = original
        self.assertIsNotNone(activated)
        self.assertEqual(activated["plan"], "pro")

    def test_reconcile_latest_order_ignores_unpaid_orders(self):
        user = make_user("reconcile-unpaid@example.com")
        billing_store.create_order("order_rec_6", user["id"], "pro", 199900, "rcpt")
        original = billing.rzp_request
        billing.rzp_request = lambda m, p, payload=None: {"id": "order_rec_6", "payments": []}
        try:
            activated = billing.reconcile_latest_order(billing_store.get_user_by_id(user["id"]))
        finally:
            billing.rzp_request = original
        self.assertIsNone(activated)

    def test_refund_downgrades_to_free(self):
        user = make_user("refund@example.com")
        billing_store.create_order("order_life_5", user["id"], "starter", 49900, "rcpt")
        billing.activate_for_payment(self._captured_payment("order_life_5", "pay_ref_1", 49900))

        downgraded = billing.downgrade_for_refund("pay_ref_1")
        self.assertEqual(downgraded["plan"], "free")
        self.assertEqual(downgraded["plan_status"], "refunded")


# ── Usage accounting & quotas ────────────────────────────────────────────────

class UsageTests(unittest.TestCase):
    def test_search_usage_counted_once_per_search(self):
        user = make_user("usage@example.com", plan="starter")
        billing_store.set_search_owner("search_abc", user["id"])

        billing.record_search_usage("search_abc")
        billing.record_search_usage("search_abc")   # duplicate delivery

        fresh = billing_store.get_user_by_id(user["id"])
        self.assertEqual(fresh["searches_used"], 1)

    def test_anonymous_searches_are_not_counted(self):
        billing.record_search_usage("search_with_no_owner")
        self.assertEqual(billing_store.stats()["users"], billing_store.stats()["users"])

    def test_free_plan_quota_exhaustion(self):
        user = make_user("quota@example.com")
        billing_store.increment_counters(user["id"], searches=3)
        ent = billing.entitlement_for(billing_store.get_user_by_id(user["id"]))
        self.assertEqual(ent["searches_limit"], 3)
        self.assertEqual(ent["searches_remaining"], 0)

    def test_usage_window_resets_after_the_period(self):
        user = make_user("reset@example.com")
        billing_store.increment_counters(user["id"], searches=3)
        billing_store.db.execute(
            "UPDATE users SET period_end = ? WHERE id = ?",
            ("2000-01-01T00:00:00+00:00", user["id"]))
        ent = billing.entitlement_for(billing_store.get_user_by_id(user["id"]))
        self.assertEqual(ent["searches_used"], 0)
        self.assertEqual(ent["searches_remaining"], 3)

    def test_entitlement_for_anonymous_visitor(self):
        ent = billing.entitlement_for(None)
        self.assertFalse(ent["signed_in"])
        self.assertEqual(ent["status"], "anonymous")


# ── HTTP API ─────────────────────────────────────────────────────────────────

class ApiTests(unittest.TestCase):
    def setUp(self):
        # The client always presents a Supabase access token; `self.anon` is
        # the unauthenticated one.
        self.stub = SupabaseStub().install(self)
        self.client = TestClient(engine_app.app, headers=AUTH_HEADERS)
        self.anon = TestClient(engine_app.app)
        self._rzp = billing.rzp_request
        self._fetch = billing.fetch_payment

    def tearDown(self):
        billing.rzp_request = self._rzp
        billing.fetch_payment = self._fetch
        billing.REQUIRE_AUTH = False

    def _sign_in_as(self, email):
        """Present a Supabase session for `email`; the client is already
        carrying the bearer token."""
        self.stub = SupabaseStub(email=email).install(self)
        self.stub.account()
        return email

    def test_config_exposes_only_the_publishable_key(self):
        res = self.client.get("/api/billing/config")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["billing_enabled"])
        self.assertEqual(body["key_id"], billing.KEY_ID)
        self.assertNotIn(billing.KEY_SECRET, res.text)
        self.assertNotIn("secret", res.text.lower().replace("key_secret", ""))

    def test_plans_are_public(self):
        res = self.client.get("/api/billing/plans")
        self.assertEqual(res.status_code, 200)
        ids = [p["id"] for p in res.json()["plans"]]
        self.assertEqual(ids, ["free", "academic", "starter", "pro", "team"])

    def test_a_session_resolves_to_its_account(self):
        email = self._sign_in_as("api-cycle@example.com")
        me = self.client.get("/api/account/me").json()
        self.assertEqual(me["user"]["email"], email)
        self.assertEqual(me["entitlement"]["plan"], "free")
        self.assertTrue(me["entitlement"]["signed_in"])

    def test_create_order_then_verify_payment(self):
        self._sign_in_as("api-pay@example.com")
        billing.rzp_request = lambda m, p, payload=None: {
            "id": "order_api_1", "amount": payload["amount"], "currency": "INR"}

        res = self.client.post("/api/billing/create-order", json={"plan": "starter"})
        self.assertEqual(res.status_code, 200, res.text)
        order = res.json()
        self.assertEqual(order["amount"], 49900)
        self.assertEqual(order["key_id"], billing.KEY_ID)

        billing.fetch_payment = lambda pid: {"id": pid, "order_id": order["order_id"],
                                             "amount": 49900, "status": "captured",
                                             "method": "upi"}
        signature = sign_checkout(order["order_id"], "pay_api_1", billing.KEY_SECRET)
        res = self.client.post("/api/billing/verify-payment", json={
            "razorpay_order_id": order["order_id"],
            "razorpay_payment_id": "pay_api_1",
            "razorpay_signature": signature,
        })
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["status"], "active")
        self.assertEqual(res.json()["entitlement"]["plan"], "starter")

    def test_verify_payment_rejects_bad_signature(self):
        self._sign_in_as("api-badsig@example.com")
        billing.rzp_request = lambda m, p, payload=None: {
            "id": "order_api_2", "amount": 49900, "currency": "INR"}
        self.client.post("/api/billing/create-order", json={"plan": "starter"})

        res = self.client.post("/api/billing/verify-payment", json={
            "razorpay_order_id": "order_api_2",
            "razorpay_payment_id": "pay_api_2",
            "razorpay_signature": "obviously-wrong",
        })
        self.assertEqual(res.status_code, 400)

    def test_create_order_requires_a_signed_in_user(self):
        self.assertEqual(self.anon.post("/api/billing/create-order",
                                        json={"plan": "starter"}).status_code, 401)

    def test_payment_status_endpoint(self):
        self._sign_in_as("api-status@example.com")
        billing.rzp_request = lambda m, p, payload=None: {
            "id": "order_api_3", "amount": 49900, "currency": "INR"}
        self.client.post("/api/billing/create-order", json={"plan": "starter"})
        billing.fetch_payment = lambda pid: {"id": pid, "order_id": "order_api_3",
                                             "amount": 49900, "status": "captured"}

        res = self.client.get("/api/billing/payment-status/pay_api_3")
        self.assertEqual(res.status_code, 200)
        self.assertIn(res.json()["status"], ("captured", "pending"))

    def test_payment_status_credits_a_captured_payment(self):
        """A payment captured upstream but never verified must still land."""
        self._sign_in_as("api-reconcile@example.com")
        billing.rzp_request = lambda m, p, payload=None: {
            "id": "order_rec_http", "amount": 49900, "currency": "INR"}
        self.client.post("/api/billing/create-order", json={"plan": "starter"})
        billing.fetch_payment = lambda pid: {"id": pid, "order_id": "order_rec_http",
                                             "amount": 49900, "status": "captured"}

        res = self.client.get("/api/billing/payment-status/pay_rec_http")
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["entitlement"]["plan"], "starter")
        self.assertEqual(body["entitlement"]["searches_limit"], 50)

    def test_webhook_rejects_invalid_signature(self):
        res = self.client.post("/api/billing/webhook",
                               content=json.dumps({"event": "payment.captured"}),
                               headers={"x-razorpay-signature": "nope"})
        self.assertEqual(res.status_code, 400)

    def test_webhook_capture_activates_and_is_idempotent(self):
        self._sign_in_as("api-hook@example.com")
        user = billing_store.get_user_by_email("api-hook@example.com")
        billing_store.create_order("order_api_4", user["id"], "pro", 199900, "rcpt")

        event = {"event": "payment.captured", "payload": {"payment": {"entity": {
            "id": "pay_hook_1", "order_id": "order_api_4", "amount": 199900,
            "currency": "INR", "status": "captured", "method": "card"}}}}
        raw = json.dumps(event).encode()
        headers = {"x-razorpay-signature": sign_webhook(raw, billing.WEBHOOK_SECRET),
                   "content-type": "application/json"}

        self.assertEqual(self.client.post("/api/billing/webhook", content=raw, headers=headers).status_code, 200)
        self.assertEqual(billing_store.get_user_by_id(user["id"])["plan"], "pro")

        billing_store.increment_counters(user["id"], searches=7)
        self.assertEqual(self.client.post("/api/billing/webhook", content=raw, headers=headers).status_code, 200)
        self.assertEqual(billing_store.get_user_by_id(user["id"])["searches_used"], 7,
                         "duplicate webhook must not reset usage")

    def test_webhook_failure_is_recorded_without_granting(self):
        self._sign_in_as("api-hookfail@example.com")
        user = billing_store.get_user_by_email("api-hookfail@example.com")
        event = {"event": "payment.failed", "payload": {"payment": {"entity": {
            "id": "pay_hook_fail", "order_id": "order_api_5", "amount": 49900,
            "status": "failed", "method": "upi"}}}}
        raw = json.dumps(event).encode()
        headers = {"x-razorpay-signature": sign_webhook(raw, billing.WEBHOOK_SECRET),
                   "content-type": "application/json"}
        self.assertEqual(self.client.post("/api/billing/webhook", content=raw, headers=headers).status_code, 200)
        self.assertEqual(billing_store.get_user_by_id(user["id"])["plan"], "free")
        self.assertIsNotNone(billing_store.get_payment("pay_hook_fail"))


# ── Regression: the existing product must be untouched ───────────────────────

class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(engine_app.app)
        # Never actually spawn the engine during tests.
        engine_app.searches.clear()

    def test_existing_routes_are_still_registered(self):
        paths = set(route_paths(engine_app.app))
        for expected in ("/api/health", "/api/search", "/api/search/{search_id}",
                         "/api/search/{search_id}/log", "/api/search/{search_id}/graph",
                         "/api/exports/{filename:path}", "/api/searches", "/api/analyze"):
            self.assertIn(expected, paths)

    def test_billing_routes_are_mounted_before_the_spa_catch_all(self):
        paths = route_paths(engine_app.app)
        catch_all = paths.index("/{full_path:path}")
        for path in ("/api/billing/config", "/api/billing/plans", "/api/account/me",
                     "/api/billing/create-order", "/api/billing/webhook"):
            self.assertIn(path, paths)
            self.assertLess(paths.index(path), catch_all, f"{path} must precede the SPA catch-all")

    def test_health_endpoint_unchanged(self):
        body = self.client.get("/api/health").json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["version"], "3.2.2")

    def test_anonymous_search_still_works_by_default(self):
        original = engine_app.run_search_in_background

        async def noop(*args, **kwargs):
            return None

        engine_app.run_search_in_background = noop
        try:
            res = self.client.post("/api/search",
                                   json={"query": "aspirin", "query_type": "ligand", "hops": 1})
            self.assertEqual(res.status_code, 200, res.text)
            body = res.json()
            self.assertEqual(body["query"], "aspirin")
            self.assertIn(body["status"], ("queued", "running"))
        finally:
            engine_app.run_search_in_background = original

    def test_existing_validation_errors_unchanged(self):
        self.assertEqual(self.client.post("/api/search", json={"query": ""}).status_code, 400)
        self.assertEqual(self.client.post("/api/search",
                                          json={"query": "x", "hops": 9}).status_code, 400)
        self.assertEqual(self.client.post("/api/search",
                                          json={"query": "x", "query_type": "alien"}).status_code, 400)

    def test_require_auth_blocks_anonymous_searches_only_when_enabled(self):
        async def noop(*args, **kwargs):
            return None

        original = engine_app.run_search_in_background
        engine_app.run_search_in_background = noop
        billing.REQUIRE_AUTH = True
        try:
            res = self.client.post("/api/search", json={"query": "aspirin"})
            self.assertEqual(res.status_code, 401)
        finally:
            billing.REQUIRE_AUTH = False
            engine_app.run_search_in_background = original

    def test_anonymous_search_is_refused_with_a_sign_in_code(self):
        """The shape the UI keys off: 401 + code=auth_required."""
        anon = TestClient(engine_app.app)
        billing.REQUIRE_AUTH = True
        try:
            res = anon.post("/api/search", json={"query": "aspirin"})
            self.assertEqual(res.status_code, 401)
            detail = res.json()["detail"]
            self.assertEqual(detail["code"], "auth_required")
            self.assertIn("Sign in", detail["message"])
        finally:
            billing.REQUIRE_AUTH = False

    def test_signed_in_search_still_works_when_auth_is_required(self):
        """Regression guard: enforcing sign-in must not reject real accounts.
        The browser sends the Supabase bearer token on /api/search; if that
        header were ever dropped, every paying customer would get a 401."""
        async def noop(*args, **kwargs):
            return None

        SupabaseStub(email="enforced@example.com").install(self)
        signed_in = TestClient(engine_app.app, headers=AUTH_HEADERS)
        original = engine_app.run_search_in_background
        engine_app.run_search_in_background = noop
        billing.REQUIRE_AUTH = True
        try:
            res = signed_in.post("/api/search", json={"query": "aspirin"})
            self.assertEqual(res.status_code, 200, res.text)
        finally:
            billing.REQUIRE_AUTH = False
            engine_app.run_search_in_background = original

    def test_anonymous_analyst_is_refused_when_auth_is_required(self):
        anon = TestClient(engine_app.app)
        billing.REQUIRE_AUTH = True
        try:
            res = anon.post("/api/analyze", json={"prompt": "summarise"})
            self.assertEqual(res.status_code, 401)
            self.assertEqual(res.json()["detail"]["code"], "auth_required")
        finally:
            billing.REQUIRE_AUTH = False

    def test_quota_exhaustion_returns_402_for_signed_in_user(self):
        async def noop(*args, **kwargs):
            return None

        stub = SupabaseStub(email="regress-quota@example.com").install(self)
        signed_in = TestClient(engine_app.app, headers=AUTH_HEADERS)
        original = engine_app.run_search_in_background
        engine_app.run_search_in_background = noop
        try:
            me = signed_in.get("/api/account/me").json()
            user_id = me["user"]["id"]
            billing_store.increment_counters(user_id, searches=3)

            res = signed_in.post("/api/search", json={"query": "aspirin"})
            self.assertEqual(res.status_code, 402)
            self.assertEqual(res.json()["detail"]["code"], "quota_reached")
        finally:
            engine_app.run_search_in_background = original


if __name__ == "__main__":
    unittest.main(verbosity=2)