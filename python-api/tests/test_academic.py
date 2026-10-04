#!/usr/bin/env python3
"""
Academic verification, payment integrity and access-control tests.

Run:  cd python-api && python3 -m pytest tests/test_academic.py -q

Covers the test plan for the Academic plan end to end: verification ->
checkout -> activation, the refusals (ineligible email, unverified account,
expired/tampered attestation), payment signature and webhook integrity,
duplicate/replayed events, cross-account access, quota enforcement, and the
bypass attempts that matter — posting straight to create-order, replaying
another user's token, and buying an Academic plan whose verification was later
revoked by changing the profile address.
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

_TEST_DIR = tempfile.mkdtemp(prefix="scigraph_academic_test_")
os.environ["BILLING_DB"] = str(Path(_TEST_DIR) / "billing.db")
os.environ["SEARCH_DB"] = str(Path(_TEST_DIR) / "search.db")
os.environ["EXPORTS_DIR"] = str(Path(_TEST_DIR) / "exports")
os.environ["WORKSPACE_DIR"] = str(Path(_TEST_DIR) / "workspace")
os.environ["RAZORPAY_KEY_ID"] = "rzp_test_key_id"
os.environ["RAZORPAY_KEY_SECRET"] = "rzp_test_key_secret"
os.environ["RAZORPAY_WEBHOOK_SECRET"] = "rzp_test_webhook_secret"
os.environ["ACADEMIC_VERIFICATION_SECRET"] = "test_academic_signing_secret"
os.environ["REQUIRE_AUTH"] = "0"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import academic  # noqa: E402
import auth  # noqa: E402
import billing  # noqa: E402
import billing_store  # noqa: E402
from db import db as store  # noqa: E402
from support import AUTH_HEADERS, SupabaseStub  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app as engine_app  # noqa: E402


# ── Stubbed Razorpay ─────────────────────────────────────────────────────────

class FakeRazorpay:
    """Stands in for the provider so no test touches the network.

    Orders behave like real ones; payments are captured only when explicitly
    asked for, so the 'payment failed / cancelled' paths are reachable.
    """

    def __init__(self):
        self.orders = {}
        self.payments = {}
        self.fail_next_order = False

    def request(self, method, path, payload=None):
        if method == "POST" and path == "/orders":
            if self.fail_next_order:
                self.fail_next_order = False
                raise billing.RazorpayError("The payment provider rejected the request", 502)
            # Unique across the whole process: `orders.order_id` is UNIQUE and
            # create_order does ON CONFLICT DO NOTHING, so a per-test counter
            # would silently hand a later test an earlier test's order row.
            order_id = "order_test" + uuid.uuid4().hex[:16]
            order = {"id": order_id, "amount": payload["amount"],
                     "currency": payload["currency"], "status": "created",
                     "receipt": payload.get("receipt")}
            self.orders[order_id] = order
            return order
        if method == "POST" and path.startswith("/payments/"):
            self.payments[path.rsplit("/", 1)[1]] = payload
            return payload
        if method == "GET" and path.startswith("/payments/"):
            pid = path.rsplit("/", 1)[1]
            if pid not in self.payments:
                raise billing.RazorpayError("Invalid payment reference", 400)
            return self.payments[pid]
        if method == "GET" and path.startswith("/orders/"):
            return self.orders.get(path.rsplit("/", 1)[1], {})
        raise billing.RazorpayError("Unexpected provider call", 502)

    def capture(self, order_id, amount=24900, status="captured", pid=None):
        pid = pid or "pay_test" + uuid.uuid4().hex[:16]
        payment = {"id": pid, "order_id": order_id, "amount": amount,
                   "currency": "INR", "status": status, "method": "card"}
        self.payments[pid] = payment
        return payment

    def webhook_body(self, event, payment):
        raw = json.dumps({"event": event,
                          "payload": {"payment": {"entity": payment}}}).encode()
        sig = hmac.new(b"rzp_test_webhook_secret", raw, hashlib.sha256).hexdigest()
        return raw, sig


class AcademicTestCase(unittest.TestCase):
    def setUp(self):
        self.rzp = FakeRazorpay()
        self._request = billing.rzp_request
        self._fetch = billing.fetch_payment
        billing.rzp_request = self.rzp.request
        billing.fetch_payment = lambda pid: self.rzp.request("GET", f"/payments/{pid}")
        self._auth = billing.REQUIRE_AUTH
        billing.REQUIRE_AUTH = True
        self.stub = SupabaseStub().install(self)
        self.client = TestClient(engine_app.app, headers=AUTH_HEADERS)
        self.account = self.stub.account()
        # One institutional address may back only one Academic subscription, so
        # every test claims its own — otherwise the duplicate guard (correctly)
        # refuses the second test with a 409.
        self.academic_email = f"scholar-{uuid.uuid4().hex[:10]}@university.edu"

    def tearDown(self):
        billing.rzp_request = self._request
        billing.fetch_payment = self._fetch
        billing.REQUIRE_AUTH = self._auth

    # ── helpers ──
    def verify(self, email=None, expect_ok=True, **extra):
        email = email or self.academic_email
        payload = {"academic_email": email, "institute_name": "Test University",
                   "institute_id": "INST-1", "academician_type": "Research Scholar"}
        payload.update(extra)
        res = self.client.post("/api/account/academic/verify", json=payload)
        return res

    def buy(self, plan="academic", token="auto", **kwargs):
        if token == "auto":
            token = self.verify().json().get("token")
        return self.client.post("/api/billing/create-order",
                                json={"plan": plan, "academic_verification_token": token},
                                **kwargs)


# ── 1. Verification → payment → activation ───────────────────────────────────

class AcademicHappyPath(AcademicTestCase):
    def test_verify_then_buy_then_activation_grants_the_plan(self):
        res = self.verify()
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["status"], "verified")
        self.assertTrue(body["token"])
        self.assertEqual(body["verification"]["status"], "verified")
        self.assertEqual(body["verification"]["verified_email"], self.academic_email)

        order_res = self.buy(token=body["token"])
        self.assertEqual(order_res.status_code, 200, order_res.text)
        order_id = order_res.json()["order_id"]

        payment = self.rzp.capture(order_id)
        sig = hmac.new(b"rzp_test_key_secret",
                       f"{order_id}|{payment['id']}".encode(), hashlib.sha256).hexdigest()
        vres = self.client.post("/api/billing/verify-payment", json={
            "razorpay_order_id": order_id, "razorpay_payment_id": payment["id"],
            "razorpay_signature": sig})
        self.assertEqual(vres.status_code, 200, vres.text)
        self.assertEqual(vres.json()["status"], "active")
        self.assertEqual(vres.json()["entitlement"]["plan"], "academic")
        self.assertEqual(vres.json()["entitlement"]["searches_limit"], 30)

        me = self.client.get("/api/account/me").json()
        self.assertEqual(me["entitlement"]["plan_name"], "Academic")

    def test_non_academic_plans_do_not_require_verification(self):
        for plan in ("starter", "pro", "team"):
            with self.subTest(plan=plan):
                res = self.buy(plan=plan, token=None)
                self.assertEqual(res.status_code, 200, res.text)
                self.assertEqual(res.json()["plan"], plan)

    def test_free_plan_never_takes_payment(self):
        res = self.client.post("/api/billing/create-order", json={"plan": "free"})
        self.assertEqual(res.status_code, 400)


# ── 2. Verification failure → payment denied ─────────────────────────────────

class AcademicRefusals(AcademicTestCase):
    def test_consumer_email_is_rejected_and_cannot_buy(self):
        for email in ("me@gmail.com", "me@outlook.com", "me@yahoo.co.uk", "me@proton.me"):
            with self.subTest(email=email):
                res = self.verify(email=email)
                self.assertEqual(res.status_code, 422, res.text)
                self.assertEqual(res.json()["detail"]["code"], "academic_not_eligible")
                self.assertNotIn("token", res.json())
        self.assertEqual(self.buy(token=None).status_code, 403)

    def test_non_institution_domain_is_rejected(self):
        res = self.verify(email="someone@company.com")
        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.json()["detail"]["code"], "academic_not_eligible")

    def test_subdomain_of_consumer_provider_is_rejected(self):
        """A bypass the suffix check alone would have waved through."""
        res = self.verify(email="researcher@students.gmail.com")
        self.assertEqual(res.status_code, 422)

    def test_malformed_email_is_rejected_without_a_stack_trace(self):
        res = self.verify(email="not-an-email")
        self.assertIn(res.status_code, (400, 422))
        self.assertNotIn("Traceback", res.text)

    def test_unverified_account_cannot_create_an_academic_order(self):
        """Skipping the UI entirely and posting plan='academic' directly."""
        res = self.buy(token=None)
        self.assertEqual(res.status_code, 403, res.text)
        self.assertEqual(res.json()["detail"]["code"], "academic_verification_required")

    def test_forged_token_is_refused(self):
        self.verify()
        forged = academic.issue_token(self.account["id"], self.academic_email)[:-4] + "aaaa"
        self.assertEqual(self.buy(token=forged).status_code, 403)

    def test_token_from_another_account_is_refused(self):
        self.verify()
        other = SupabaseStub(email="other@university.edu").install(self)
        other_account = other.account()
        stolen = academic.issue_token(other_account["id"], self.academic_email)
        # The token is signed for another user, and this account is verified
        # for a different address, so it must not open checkout.
        self.assertEqual(self.buy(token=stolen).status_code, 403)

    def test_expired_token_is_refused(self):
        self.verify()
        stale = academic.issue_token(self.account["id"], self.academic_email, ttl=-10)
        res = self.buy(token=stale)
        self.assertEqual(res.status_code, 403)
        self.assertIn("expired", res.json()["detail"]["message"].lower())

    def test_duplicate_academic_email_on_a_second_account_is_refused(self):
        self.verify()
        second = SupabaseStub(email="second@elsewhere.edu").install(self)
        second.account()
        signed_in = TestClient(engine_app.app, headers=AUTH_HEADERS)
        res = signed_in.post("/api/account/academic/verify", json={
            "academic_email": self.academic_email, "institute_name": "X"})
        self.assertEqual(res.status_code, 409, res.text)
        self.assertEqual(res.json()["detail"]["code"], "academic_already_claimed")

    def test_changing_the_profile_email_revokes_the_verification(self):
        self.verify()
        self.assertTrue(academic.is_verified(
            billing_store.get_user_by_id(self.account["id"])))
        self.client.put("/api/account/profile",
                        json={"academic_email": "someone.else@elsewhere.edu"})
        self.assertFalse(academic.is_verified(
            billing_store.get_user_by_id(self.account["id"])))
        self.assertEqual(self.buy(token=None).status_code, 403)

    def test_verification_attempts_are_rate_limited(self):
        saved = academic.MAX_ATTEMPTS_PER_HOUR
        academic.MAX_ATTEMPTS_PER_HOUR = 2
        try:
            self.verify(email="a@university.edu")
            self.verify(email="b@university.edu")
            res = self.verify(email="c@university.edu")
            self.assertEqual(res.status_code, 429)
            self.assertEqual(res.json()["detail"]["code"], "academic_rate_limited")
        finally:
            academic.MAX_ATTEMPTS_PER_HOUR = saved

    def test_anonymous_cannot_verify(self):
        anon = TestClient(engine_app.app)
        res = anon.post("/api/account/academic/verify",
                        json={"academic_email": "x@university.edu"})
        self.assertEqual(res.status_code, 401)


# ── 3/4/5. Payment outcomes, signatures, webhooks, replays ───────────────────

class PaymentIntegrity(AcademicTestCase):
    def _verified_token(self):
        return self.verify().json()["token"]

    def _order(self, plan="academic"):
        token = self._verified_token() if plan == "academic" else None
        res = self.client.post("/api/billing/create-order",
                               json={"plan": plan, "academic_verification_token": token})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["order_id"]

    def test_invalid_checkout_signature_is_rejected(self):
        order_id = self._order()
        payment = self.rzp.capture(order_id)
        res = self.client.post("/api/billing/verify-payment", json={
            "razorpay_order_id": order_id, "razorpay_payment_id": payment["id"],
            "razorpay_signature": "deadbeef" * 8})
        self.assertEqual(res.status_code, 400)
        me = self.client.get("/api/account/me").json()
        self.assertEqual(me["entitlement"]["plan"], "free")

    def test_failed_payment_does_not_activate(self):
        order_id = self._order()
        payment = self.rzp.capture(order_id, status="failed")
        sig = hmac.new(b"rzp_test_key_secret",
                       f"{order_id}|{payment['id']}".encode(), hashlib.sha256).hexdigest()
        res = self.client.post("/api/billing/verify-payment", json={
            "razorpay_order_id": order_id, "razorpay_payment_id": payment["id"],
            "razorpay_signature": sig})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.client.get("/api/account/me").json()["entitlement"]["plan"], "free")

    def test_cancelled_payment_does_not_activate(self):
        order_id = self._order()
        payment = self.rzp.capture(order_id, status="cancelled")
        sig = hmac.new(b"rzp_test_key_secret",
                       f"{order_id}|{payment['id']}".encode(), hashlib.sha256).hexdigest()
        res = self.client.post("/api/billing/verify-payment", json={
            "razorpay_order_id": order_id, "razorpay_payment_id": payment["id"],
            "razorpay_signature": sig})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.client.get("/api/account/me").json()["entitlement"]["plan"], "free")

    def test_payment_for_a_different_order_is_refused(self):
        order_a = self._order()
        order_b = self._order()
        payment = self.rzp.capture(order_a)
        sig = hmac.new(b"rzp_test_key_secret",
                       f"{order_b}|{payment['id']}".encode(), hashlib.sha256).hexdigest()
        res = self.client.post("/api/billing/verify-payment", json={
            "razorpay_order_id": order_b, "razorpay_payment_id": payment["id"],
            "razorpay_signature": sig})
        self.assertEqual(res.status_code, 400)

    def test_another_account_cannot_verify_our_order(self):
        order_id = self._order()
        payment = self.rzp.capture(order_id)
        SupabaseStub(email="thief@example.com").install(self)
        thief = TestClient(engine_app.app, headers=AUTH_HEADERS)
        sig = hmac.new(b"rzp_test_key_secret",
                       f"{order_id}|{payment['id']}".encode(), hashlib.sha256).hexdigest()
        res = thief.post("/api/billing/verify-payment", json={
            "razorpay_order_id": order_id, "razorpay_payment_id": payment["id"],
            "razorpay_signature": sig})
        self.assertEqual(res.status_code, 400)
        self.assertIn("does not belong", res.json()["detail"])

    def test_repeated_verification_of_one_payment_is_idempotent(self):
        order_id = self._order()
        payment = self.rzp.capture(order_id)
        sig = hmac.new(b"rzp_test_key_secret",
                       f"{order_id}|{payment['id']}".encode(), hashlib.sha256).hexdigest()
        body = {"razorpay_order_id": order_id, "razorpay_payment_id": payment["id"],
                "razorpay_signature": sig}
        for _ in range(3):
            self.assertEqual(
                self.client.post("/api/billing/verify-payment", json=body).status_code, 200)
        rows = store.query_one(
            "SELECT COUNT(*) AS n FROM subscriptions WHERE razorpay_payment_id = ?",
            (payment["id"],))
        self.assertEqual(int(rows["n"]), 1)
        self.assertEqual(self.client.get("/api/account/me").json()["entitlement"]["plan"],
                         "academic")

    def test_partial_payment_does_not_activate_the_plan(self):
        """Razorpay can settle less than the order total. A ₹1 payment must
        not open a ₹249 Academic plan."""
        order_id = self._order()
        payment = self.rzp.capture(order_id, amount=100)
        sig = hmac.new(b"rzp_test_key_secret",
                       f"{order_id}|{payment['id']}".encode(), hashlib.sha256).hexdigest()
        res = self.client.post("/api/billing/verify-payment", json={
            "razorpay_order_id": order_id, "razorpay_payment_id": payment["id"],
            "razorpay_signature": sig})
        self.assertEqual(res.status_code, 400, res.text)
        me = self.client.get("/api/account/me").json()
        self.assertEqual(me["entitlement"]["plan"], "free")
        self.assertEqual(me["entitlement"]["plan_name"], "Free")
        record = billing_store.get_payment(payment["id"])
        self.assertEqual(record["status"], "captured_amount_mismatch")

    def test_wrong_currency_does_not_activate_the_plan(self):
        order_id = self._order()
        payment = self.rzp.capture(order_id)
        payment["currency"] = "USD"
        sig = hmac.new(b"rzp_test_key_secret",
                       f"{order_id}|{payment['id']}".encode(), hashlib.sha256).hexdigest()
        res = self.client.post("/api/billing/verify-payment", json={
            "razorpay_order_id": order_id, "razorpay_payment_id": payment["id"],
            "razorpay_signature": sig})
        self.assertEqual(res.status_code, 400, res.text)
        self.assertEqual(self.client.get("/api/account/me").json()["entitlement"]["plan"], "free")

    def test_a_short_payment_cannot_activate_via_the_webhook(self):
        """The webhook never goes through the checkout signature check, so the
        amount comparison has to hold on this path too."""
        order_id = self._order()
        payment = self.rzp.capture(order_id, amount=1)
        raw, sig = self.rzp.webhook_body("payment.captured", payment)
        self.client.post("/api/billing/webhook", content=raw,
                         headers={"x-razorpay-signature": sig})
        self.assertEqual(self.client.get("/api/account/me").json()["entitlement"]["plan"], "free")
        self.assertEqual(billing_store.get_payment(payment["id"])["status"],
                         "captured_amount_mismatch")

    def test_a_short_payment_cannot_activate_via_reconciliation(self):
        order_id = self._order()
        payment = self.rzp.capture(order_id, amount=100)
        res = self.client.get(f"/api/billing/payment-status/{payment['id']}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(self.client.get("/api/account/me").json()["entitlement"]["plan"], "free")

    def test_exact_amount_still_activates(self):
        """Guard against the fix being over-eager: the correct price must work."""
        order_id = self._order()
        payment = self.rzp.capture(order_id, amount=24900)
        sig = hmac.new(b"rzp_test_key_secret",
                       f"{order_id}|{payment['id']}".encode(), hashlib.sha256).hexdigest()
        res = self.client.post("/api/billing/verify-payment", json={
            "razorpay_order_id": order_id, "razorpay_payment_id": payment["id"],
            "razorpay_signature": sig})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(self.client.get("/api/account/me").json()["entitlement"]["plan"],
                         "academic")

    def test_forged_webhook_is_rejected(self):
        raw, _ = self.rzp.webhook_body("payment.captured", {"id": "pay_forged"})
        res = self.client.post("/api/billing/webhook", content=raw,
                               headers={"x-razorpay-signature": "forged"})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.client.get("/api/account/me").json()["entitlement"]["plan"], "free")

    def test_webhook_body_tampering_is_rejected(self):
        order_id = self._order()
        payment = self.rzp.capture(order_id)
        raw, sig = self.rzp.webhook_body("payment.captured", payment)
        tampered = raw.replace(b'"captured"', b'"authorised"')
        res = self.client.post("/api/billing/webhook", content=tampered,
                               headers={"x-razorpay-signature": sig})
        self.assertEqual(res.status_code, 400)

    def test_duplicate_webhook_delivery_activates_once(self):
        order_id = self._order()
        payment = self.rzp.capture(order_id)
        raw, sig = self.rzp.webhook_body("payment.captured", payment)
        for _ in range(3):
            res = self.client.post("/api/billing/webhook", content=raw,
                                   headers={"x-razorpay-signature": sig})
            self.assertEqual(res.status_code, 200, res.text)
        rows = store.query_one(
            "SELECT COUNT(*) AS n FROM subscriptions WHERE razorpay_payment_id = ?",
            (payment["id"],))
        self.assertEqual(int(rows["n"]), 1)

    def test_failed_webhook_records_the_failure_without_activating(self):
        order_id = self._order()
        payment = self.rzp.capture(order_id, status="failed")
        raw, sig = self.rzp.webhook_body("payment.failed", payment)
        self.client.post("/api/billing/webhook", content=raw,
                         headers={"x-razorpay-signature": sig})
        record = billing_store.get_payment(payment["id"])
        self.assertIsNotNone(record)
        self.assertEqual(record["status"], "failed")
        self.assertEqual(self.client.get("/api/account/me").json()["entitlement"]["plan"], "free")

    def test_webhook_cannot_activate_academic_for_an_unverified_account(self):
        """The webhook path never sees an attestation token, so this is the
        route a bypass attempt would take."""
        order_id = self._order()
        # Verification is revoked before the webhook lands.
        self.client.put("/api/account/profile",
                        json={"academic_email": "new.address@elsewhere.edu"})
        payment = self.rzp.capture(order_id)
        raw, sig = self.rzp.webhook_body("payment.captured", payment)
        self.assertEqual(self.client.post("/api/billing/webhook", content=raw,
                                          headers={"x-razorpay-signature": sig}).status_code,
                         200)
        me = self.client.get("/api/account/me").json()
        self.assertEqual(me["entitlement"]["plan"], "free")
        # The money is still traceable rather than silently dropped.
        record = billing_store.get_payment(payment["id"])
        self.assertEqual(record["status"], "captured_unverified")


# ── 7/9. Access control and quota bypass attempts ────────────────────────────

class AccessControl(AcademicTestCase):
    def test_quota_is_enforced_on_the_backend(self):
        for plan, limit in (("starter", 50), ("pro", 300), ("team", 3000)):
            with self.subTest(plan=plan):
                billing_store.set_plan(self.account["id"], plan)
                me = self.client.get("/api/account/me").json()
                self.assertEqual(me["entitlement"]["searches_limit"], limit)
                billing_store.increment_counters(self.account["id"], searches=limit)
                res = self.client.post("/api/search",
                                       json={"query": "aspirin", "query_type": "ligand"})
                self.assertEqual(res.status_code, 402, res.text)
                self.assertEqual(res.json()["detail"]["code"], "quota_reached")

    def test_parallel_searches_cannot_exceed_the_free_allowance(self):
        """Reproduces the bypass: usage was only counted on completion, so a
        free user could open many searches at once and the cap never fired."""
        async def noop(*args, **kwargs):
            return None
        original = engine_app.run_search_in_background
        engine_app.run_search_in_background = noop
        try:
            self.assertEqual(self.client.get("/api/account/me").json()
                             ["entitlement"]["searches_limit"], 3)
            statuses = [
                self.client.post("/api/search",
                                 json={"query": f"aspirin{i}", "query_type": "ligand"}).status_code
                for i in range(5)
            ]
            self.assertEqual(statuses, [200, 200, 200, 402, 402], statuses)
            ent = self.client.get("/api/account/me").json()["entitlement"]
            self.assertEqual(ent["searches_used"], 3)
            self.assertEqual(ent["searches_remaining"], 0)
        finally:
            engine_app.run_search_in_background = original

    def test_a_completed_search_frees_its_slot_exactly_once(self):
        """Finishing a job moves it from in-flight to counted — the total must
        not double-count or drift."""
        async def noop(*args, **kwargs):
            return None
        original = engine_app.run_search_in_background
        engine_app.run_search_in_background = noop
        try:
            res = self.client.post("/api/search",
                                   json={"query": "aspirin", "query_type": "ligand"})
            self.assertEqual(res.status_code, 200, res.text)
            search_id = res.json()["search_id"]
            before = self.client.get("/api/account/me").json()["entitlement"]["searches_used"]
            self.assertEqual(before, 1, "an in-flight search must occupy a slot")

            billing.record_search_usage(search_id)
            after = self.client.get("/api/account/me").json()["entitlement"]["searches_used"]
            self.assertEqual(after, 1, "completing must not double-count")

            # Replaying the completion must not inflate usage either.
            billing.record_search_usage(search_id)
            self.assertEqual(
                self.client.get("/api/account/me").json()["entitlement"]["searches_used"], 1)
        finally:
            engine_app.run_search_in_background = original

    def test_stale_in_flight_searches_do_not_consume_a_new_period(self):
        """A job left running across a plan upgrade must not eat the new quota."""
        billing_store.set_search_owner("stale_search_id", self.account["id"])
        billing_store.set_plan(self.account["id"], "starter")
        ent = self.client.get("/api/account/me").json()["entitlement"]
        self.assertEqual(ent["searches_used"], 0)
        self.assertEqual(ent["searches_remaining"], 50)

    def test_forged_token_cannot_spend_someone_elses_quota(self):
        anon = TestClient(engine_app.app)
        res = anon.post("/api/search", json={"query": "aspirin", "query_type": "ligand"})
        self.assertEqual(res.status_code, 401)

    def test_payment_status_of_another_account_is_not_readable(self):
        res = self.client.post("/api/billing/create-order", json={
            "plan": "starter", "academic_verification_token": None})
        order_id = res.json()["order_id"]
        payment = self.rzp.capture(order_id, amount=49900)
        billing_store.record_payment(payment["id"], order_id, self.account["id"],
                                     "starter", 49900, "captured", "card", captured=True)

        SupabaseStub(email="nosy@example.com").install(self)
        nosy = TestClient(engine_app.app, headers=AUTH_HEADERS)
        res = nosy.get(f"/api/billing/payment-status/{payment['id']}")
        self.assertEqual(res.status_code, 403)
        self.assertEqual(res.json()["detail"], "Not your payment")

    def test_profile_endpoints_require_a_session(self):
        anon = TestClient(engine_app.app)
        for method, path, body in (
            ("get", "/api/account/profile", None),
            ("get", "/api/account/academic/status", None),
            ("put", "/api/account/profile", {}),
            ("post", "/api/account/academic/verify", {"academic_email": "a@university.edu"}),
        ):
            with self.subTest(path=path):
                call = getattr(anon, method)
                res = call(path, json=body) if body is not None else call(path)
                self.assertEqual(res.status_code, 401, f"{method} {path} -> {res.status_code}")

    def test_account_me_stays_200_for_anonymous_by_contract(self):
        """/api/account/me is deliberately always 200 so the panel can render
        the signed-out state; it must report anonymous and leak nothing."""
        anon = TestClient(engine_app.app)
        res = anon.get("/api/account/me")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertIsNone(body["user"])
        self.assertFalse(body["entitlement"]["signed_in"])
        self.assertEqual(body["entitlement"]["status"], "anonymous")
        self.assertEqual(body["entitlement"]["searches_limit"], 0)

    def test_secret_is_never_exposed_to_the_client(self):
        text = self.client.get("/api/billing/config").text
        for secret in ("rzp_test_key_secret", "rzp_test_webhook_secret",
                       "test_academic_signing_secret"):
            self.assertNotIn(secret, text)

    def test_malformed_payment_reference_is_refused(self):
        res = self.client.get("/api/billing/payment-status/'; DROP TABLE users;--")
        self.assertIn(res.status_code, (400, 404, 422, 502))

    def test_admin_directory_stays_closed_without_a_token(self):
        res = self.client.get("/api/admin/academics")
        self.assertIn(res.status_code, (401, 404))


# ── Database consistency ─────────────────────────────────────────────────────

class Consistency(AcademicTestCase):
    def test_verification_audit_trail_is_recorded(self):
        self.verify(email="ok@university.edu")
        self.verify(email="no@gmail.com")
        row = store.query_one(
            "SELECT COUNT(*) AS n FROM academic_verifications WHERE user_id = ?",
            (self.account["id"],))
        self.assertEqual(int(row["n"]), 2)

    def test_subscription_and_user_plan_agree_after_payment(self):
        token = self.verify().json()["token"]
        order_id = self.client.post("/api/billing/create-order", json={
            "plan": "academic", "academic_verification_token": token}).json()["order_id"]
        payment = self.rzp.capture(order_id)
        raw, sig = self.rzp.webhook_body("payment.captured", payment)
        self.client.post("/api/billing/webhook", content=raw,
                         headers={"x-razorpay-signature": sig})

        user = billing_store.get_user_by_id(self.account["id"])
        self.assertEqual(user["plan"], "academic")
        self.assertEqual(user["plan_status"], "active")
        order = billing_store.get_order(order_id)
        self.assertEqual(order["status"], "paid")
        record = billing_store.get_payment(payment["id"])
        self.assertEqual(record["status"], "captured")
        self.assertEqual(record["user_id"], user["id"])

    def test_a_failed_payment_leaves_the_order_open_not_half_active(self):
        token = self.verify().json()["token"]
        order_id = self.client.post("/api/billing/create-order", json={
            "plan": "academic", "academic_verification_token": token}).json()["order_id"]
        payment = self.rzp.capture(order_id, status="failed")
        raw, sig = self.rzp.webhook_body("payment.failed", payment)
        self.client.post("/api/billing/webhook", content=raw,
                         headers={"x-razorpay-signature": sig})
        self.assertEqual(billing_store.get_order(order_id)["status"], "created")
        self.assertEqual(billing_store.get_user_by_id(self.account["id"])["plan"], "free")


# ── Eligibility rules ────────────────────────────────────────────────────────

class EligibilityRules(unittest.TestCase):
    def test_accepted_domains(self):
        for domain in ("university.edu", "mit.ac.in", "oxford.ac.uk", "melbourne.edu.au",
                       "tsinghua.edu.cn", "uni-bonn.de", "nwu.ac.za"):
            with self.subTest(domain=domain):
                self.assertTrue(academic.classify_email(f"a@{domain}")["eligible"])

    def test_refused_domains(self):
        for domain in ("gmail.com", "outlook.com", "company.com", "mail.ru",
                       "example.com", "university.ac.evil.com"):
            with self.subTest(domain=domain):
                self.assertFalse(academic.classify_email(f"a@{domain}")["eligible"])

    def test_generic_country_tlds_are_not_auto_accepted(self):
        """`.ca`/`.de` carry commercial providers too, so they are opt-in via
        the allowlist rather than matched on the TLD."""
        self.assertFalse(academic.classify_email("a@uwaterloo.ca")["eligible"])
        self.assertFalse(academic.classify_email("a@yahoo.ca")["eligible"])
        self.assertFalse(academic.classify_email("a@sympatico.ca")["eligible"])
        saved = academic.ALLOWED_DOMAINS
        academic.ALLOWED_DOMAINS = ("uwaterloo.ca",)
        try:
            self.assertTrue(academic.classify_email("a@uwaterloo.ca")["eligible"])
            # The consumer blocklist still wins over the allowlist.
            self.assertFalse(academic.classify_email("a@yahoo.ca")["eligible"])
        finally:
            academic.ALLOWED_DOMAINS = saved

    def test_explicit_allowlist_overrides_the_suffix_list(self):
        saved = academic.ALLOWED_DOMAINS
        academic.ALLOWED_DOMAINS = ("approved-institute.ac",)
        try:
            self.assertTrue(academic.classify_email("x@approved-institute.ac")["eligible"])
            self.assertFalse(academic.classify_email("y@university.edu")["eligible"])
        finally:
            academic.ALLOWED_DOMAINS = saved

    def test_token_round_trip_and_tamper_detection(self):
        token = academic.issue_token(7, "a@university.edu")
        self.assertTrue(academic.verify_token(token, 7, "a@university.edu")["ok"])
        self.assertFalse(academic.verify_token(token, 8, "a@university.edu")["ok"])
        self.assertFalse(academic.verify_token(token, 7, "b@university.edu")["ok"])
        self.assertFalse(academic.verify_token("garbage", 7)["ok"])
        self.assertFalse(academic.verify_token("", 7)["ok"])

    def test_token_cannot_be_forged_without_the_secret(self):
        saved = os.environ.get("ACADEMIC_VERIFICATION_SECRET")
        try:
            os.environ["ACADEMIC_VERIFICATION_SECRET"] = "attacker_secret"
            token = academic.issue_token(7, "a@university.edu")
        finally:
            if saved is None:
                os.environ.pop("ACADEMIC_VERIFICATION_SECRET", None)
            else:
                os.environ["ACADEMIC_VERIFICATION_SECRET"] = saved
        forged = academic.issue_token(7, "a@university.edu")
        self.assertFalse(academic.verify_token(token, 7, "a@university.edu")["ok"])
        self.assertTrue(academic.verify_token(forged, 7, "a@university.edu")["ok"])

    def test_no_signing_key_refuses_rather_than_allows(self):
        saved = os.environ.get("ACADEMIC_VERIFICATION_SECRET")
        key = os.environ.get("RAZORPAY_KEY_SECRET")
        try:
            os.environ.pop("ACADEMIC_VERIFICATION_SECRET", None)
            os.environ.pop("RAZORPAY_KEY_SECRET", None)
            self.assertIsNone(academic.issue_token(7, "a@university.edu"))
            self.assertFalse(academic.verify_token("anything", 7)["ok"])
        finally:
            if saved is not None:
                os.environ["ACADEMIC_VERIFICATION_SECRET"] = saved
            if key is not None:
                os.environ["RAZORPAY_KEY_SECRET"] = key


class AcademicEligibilityProbe(AcademicTestCase):
    """The read-only probe the account panel calls before showing the form.

    It exists so a personal address can be declined without cost. The real
    endpoint records an attempt against a five-per-hour budget and flips the
    account to "rejected", which is the wrong thing to do to someone who merely
    clicked a plan card.
    """

    def probe(self, email):
        return self.client.post("/api/account/academic/eligibility",
                                json={"email": email})

    def test_an_institutional_address_is_reported_eligible(self):
        res = self.probe(self.academic_email)
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertTrue(body["eligible"])
        self.assertEqual(body["domain"], "university.edu")
        self.assertTrue(body["reason"])

    def test_a_personal_address_is_reported_ineligible_with_a_reason(self):
        res = self.probe("someone@gmail.com")
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertFalse(body["eligible"])
        self.assertEqual(body["domain"], "gmail.com")
        self.assertTrue(body["reason"], "the UI shows this to the user")

    def test_it_never_answers_422(self):
        """422 is what /verify returns for an ineligible address; the probe has
        to be distinguishable so the UI does not treat a decline as an error."""
        self.assertEqual(self.probe("someone@gmail.com").status_code, 200)

    def test_probing_costs_no_verification_attempt(self):
        # An epoch far enough back that every recorded attempt counts.
        epoch = "1970-01-01T00:00:00"

        def attempts():
            return billing_store.count_academic_attempts_since(self.account["id"], epoch)

        baseline = attempts()
        for _ in range(5):
            self.assertEqual(self.probe("someone@gmail.com").status_code, 200)
        self.assertEqual(attempts(), baseline,
                         "the probe consumed the hourly verification budget")

        # Control: without this the assertion above would also pass if the
        # counter were simply dead and always read zero.
        self.verify(email="someone@gmail.com")
        self.assertGreater(attempts(), baseline,
                           "the attempt counter does not move on a real verify, "
                           "so the assertion above proves nothing")

    def test_probing_does_not_reject_the_account(self):
        self.probe("someone@gmail.com")
        self.assertFalse(academic.is_verified(
            billing_store.get_user_by_id(self.account["id"])))

    def test_a_rejected_account_may_still_probe_and_recover(self):
        """Declining a card must not close the door on a later real address."""
        self.assertEqual(self.verify(email="someone@gmail.com").status_code, 422)
        self.probe(self.academic_email)
        res = self.verify(email=self.academic_email)
        self.assertEqual(res.status_code, 200, res.text)
        self.assertTrue(res.json()["verification"]["verified_email"])

    def test_a_malformed_address_is_reported_not_rejected(self):
        res = self.probe("not-an-email")
        self.assertEqual(res.status_code, 200)
        self.assertFalse(res.json()["eligible"])
        self.assertTrue(res.json()["reason"])

    def test_the_probe_requires_a_signed_in_account(self):
        anonymous = TestClient(engine_app.app)
        res = anonymous.post("/api/account/academic/eligibility",
                             json={"email": self.academic_email})
        self.assertIn(res.status_code, (401, 403))

    def test_the_probe_agrees_with_the_check_it_precedes(self):
        """A probe that disagrees with /verify would hide a real academic."""
        for email in (self.academic_email,
                      "someone@gmail.com",
                      "someone@yahoo.co.uk",
                      "a.student@dept.university.edu",
                      "someone@sympatico.ca",
                      "not-an-email"):
            with self.subTest(email=email):
                probed = self.probe(email).json()["eligible"]
                checked = academic.classify_email(email)["eligible"]
                self.assertEqual(probed, checked)


if __name__ == "__main__":
    unittest.main(verbosity=2)