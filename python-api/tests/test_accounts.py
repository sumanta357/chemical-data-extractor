#!/usr/bin/env python3
"""
Tests for the Academic directory, email verification and password reset.

Run:  cd python-api && python3 -m pytest tests/test_accounts.py -q

No network: Resend is stubbed. Covers per-plan quota accumulation (Academic /
Professional "R&D" / Team), the academic profile fields, the filterable and
sortable directory, admin-token protection, and the full verify + reset flows.
"""

import itertools
import os
import sys
import tempfile
import unittest
from pathlib import Path

_TEST_DIR = tempfile.mkdtemp(prefix="scigraph_accounts_test_")
os.environ["BILLING_DB"] = str(Path(_TEST_DIR) / "billing.db")
os.environ["SEARCH_DB"] = str(Path(_TEST_DIR) / "search.db")
os.environ["EXPORTS_DIR"] = str(Path(_TEST_DIR) / "exports")
os.environ["WORKSPACE_DIR"] = str(Path(_TEST_DIR) / "workspace")
os.environ["RAZORPAY_KEY_ID"] = "rzp_test_key_id"
os.environ["RAZORPAY_KEY_SECRET"] = "rzp_test_key_secret"
os.environ["RAZORPAY_WEBHOOK_SECRET"] = "rzp_test_webhook_secret"
os.environ["COOKIE_SECURE"] = "0"
# Surface the emailed links in the response so the flows can be driven without
# a mail provider (see emailer.EXPOSE_LINKS).
os.environ["EMAIL_EXPOSE_LINKS"] = "1"
os.environ.pop("REQUIRE_AUTH", None)
os.environ.pop("REQUIRE_VERIFIED_EMAIL", None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import auth  # noqa: E402
import billing  # noqa: E402
import billing_store  # noqa: E402
import emailer  # noqa: E402
import routes_billing  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app as engine_app  # noqa: E402

PASSWORD = "correct-horse-battery"
ADMIN = {"X-Admin-Token": "admin-test-token"}
_counter = itertools.count()


def unique_email(prefix: str) -> str:
    """Every test gets its own address: the suite shares one database."""
    return f"{prefix}-{next(_counter)}@example.com"


def make_academic(email: str, **profile) -> dict:
    user = billing_store.create_user(email, auth.hash_password(PASSWORD))
    user = billing_store.set_plan(user["id"], "academic")
    if profile:
        billing_store.update_profile(user["id"], profile)
    return billing_store.get_user_by_id(user["id"])


def stub_email(testcase):
    """Capture outbound mail instead of calling the provider."""
    sent = []
    original = emailer.send
    emailer.send = lambda to, subject, html, text: (sent.append(to) or True)
    testcase.addCleanup(lambda: setattr(emailer, "send", original))
    return sent


class QuotaAccumulationTests(unittest.TestCase):
    """Two searches must add up to two on every plan, across sessions."""

    def _run_searches(self, user, count):
        """Drive the real metering path: attach the owner, then complete it."""
        for i in range(count):
            search_id = f"s-{user['id']}-{i}"
            billing.attach_search_owner(search_id, user)
            billing.record_search_usage(search_id)
        return billing_store.get_user_by_id(user["id"])

    def test_two_searches_sum_to_two_on_every_paid_plan(self):
        for plan in ("academic", "pro", "team"):
            user = billing_store.create_user(f"quota-{plan}-{next(_counter)}@example.com",
                                             auth.hash_password(PASSWORD))
            user = billing_store.set_plan(user["id"], plan)
            after = self._run_searches(user, 2)
            self.assertEqual(after["searches_used"], 2, f"{plan} did not accumulate")
            ent = billing.entitlement_for(after)
            self.assertEqual(ent["searches_used"], 2)
            self.assertEqual(ent["searches_remaining"], ent["searches_limit"] - 2)

    def test_repeat_completion_of_one_search_counts_once(self):
        user = billing_store.create_user(unique_email("once"), auth.hash_password(PASSWORD))
        user = billing_store.set_plan(user["id"], "team")
        billing.attach_search_owner(f"s-{user['id']}-once", user)
        billing.record_search_usage(f"s-{user['id']}-once")
        billing.record_search_usage(f"s-{user['id']}-once")
        self.assertEqual(billing_store.get_user_by_id(user["id"])["searches_used"], 1)

    def test_usage_survives_signing_out_and_back_in(self):
        user = billing_store.create_user(unique_email("persist"), auth.hash_password(PASSWORD))
        user = billing_store.set_plan(user["id"], "academic")
        self._run_searches(user, 2)
        fresh = billing_store.get_session_user(
            billing_store.create_session(user["id"], "pytest", 1)
        )
        self.assertEqual(fresh["searches_used"], 2)
        self.assertEqual(billing.entitlement_for(fresh)["searches_used"], 2)

    def test_team_allows_many_more_than_academic(self):
        academic = billing_store.set_plan(
            billing_store.create_user(unique_email("a"), auth.hash_password(PASSWORD))["id"],
            "academic")
        team = billing_store.set_plan(
            billing_store.create_user(unique_email("t"), auth.hash_password(PASSWORD))["id"],
            "team")
        self.assertGreater(billing.entitlement_for(team)["searches_limit"],
                           billing.entitlement_for(academic)["searches_limit"])


class ProfileTests(unittest.TestCase):
    def test_valid_profile_round_trips(self):
        user = make_academic(unique_email("prof"), name="A. Researcher",
                             academic_email="a.researcher@uni.edu",
                             institute_name="University of Example",
                             institute_id="UNI-0042",
                             phone="+91 98765 43210",
                             academician_type="Associate Professor")
        profile = billing_store.get_profile(user["id"])
        self.assertEqual(profile["institute_name"], "University of Example")
        self.assertEqual(profile["institute_id"], "UNI-0042")
        self.assertEqual(profile["phone"], "+91 98765 43210")
        self.assertEqual(profile["academician_type"], "Associate Professor")
        self.assertEqual(profile["academic_email"], "a.researcher@uni.edu")
        self.assertEqual(profile["plan"], "academic")
        self.assertFalse(profile["email_verified"])

    def test_unknown_keys_are_ignored(self):
        user = make_academic(unique_email("nokey"))
        updated = billing_store.update_profile(user["id"], {
            "institute_name": "Somewhere", "password_hash": "x", "id": 999,
        })
        self.assertEqual(updated["institute_name"], "Somewhere")
        # A smuggled field must not have been written anywhere.
        row = billing_store.get_user_by_id(user["id"])
        self.assertNotEqual(row["password_hash"], "x")
        self.assertEqual(row["id"], user["id"])

    def test_invalid_values_are_rejected(self):
        for bad in ({"phone": "not-a-phone"},
                    {"academician_type": "Dean of Deans"},
                    {"academic_email": "broken@"}):
            with self.assertRaises(Exception):
                auth.validate_profile(bad)

    def test_every_advertised_academician_type_is_accepted(self):
        for kind in billing_store.ACADEMICIAN_TYPES:
            fields = auth.validate_profile({"academician_type": kind})
            self.assertEqual(fields["academician_type"], kind)


class DirectoryTests(unittest.TestCase):
    """The three fixture customers, isolated by a marker in their institute id
    so a shared database cannot leak other academics into the assertions."""

    MARKER = "DIRTEST"

    @classmethod
    def setUpClass(cls):
        cls.fixtures = [
            ("zoe", "Delta Institute", "DEL-1", "+91 90000 00001", "Professor"),
            ("adam", "Alpha University", "ALP-2", "+91 90000 00002", "PhD Scholar"),
            ("mary", "Charlie College", "CHA-3", "+91 90000 00003", "Professor"),
        ]
        for who, institute, inst_id, phone, kind in cls.fixtures:
            make_academic(f"{who}@uni-{who}.edu",
                          academic_email=f"{who}@uni-{who}.edu",
                          institute_name=institute,
                          institute_id=f"{cls.MARKER}-{inst_id}",
                          phone=phone, academician_type=kind)
        # A paying non-academic customer must not leak into the directory.
        billing_store.set_plan(
            billing_store.create_user(unique_email("team"), auth.hash_password(PASSWORD))["id"],
            "team")

    def _list(self, **kwargs):
        kwargs.setdefault("institute_id", self.MARKER)
        kwargs.setdefault("limit", 50)
        return billing_store.list_academic_customers(**kwargs)

    def test_lists_only_academic_customers(self):
        rows = self._list()["academics"]
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(r["plan"] == "academic" for r in rows))

    def test_filter_by_institute_name(self):
        rows = self._list(institute_name="Alpha")["academics"]
        self.assertEqual([r["email"] for r in rows], ["adam@uni-adam.edu"])

    def test_filter_by_institute_id(self):
        rows = self._list(institute_id="CHA")["academics"]
        self.assertEqual([r["email"] for r in rows], ["mary@uni-mary.edu"])

    def test_filter_by_academician_type(self):
        rows = self._list(academician_type="Professor")["academics"]
        self.assertEqual({r["email"] for r in rows}, {"zoe@uni-zoe.edu", "mary@uni-mary.edu"})

    def test_filter_by_phone_and_academic_email(self):
        self.assertEqual(self._list(phone="00002")["academics"][0]["email"],
                         "adam@uni-adam.edu")
        self.assertEqual(self._list(academic_email="mary@")["academics"][0]["email"],
                         "mary@uni-mary.edu")

    def test_free_text_search_spans_fields(self):
        rows = self._list(q="Delta")["academics"]
        self.assertEqual([r["email"] for r in rows], ["zoe@uni-zoe.edu"])

    def test_combined_filters_narrow_the_result(self):
        rows = self._list(academician_type="Professor",
                          institute_name="Charlie")["academics"]
        self.assertEqual([r["email"] for r in rows], ["mary@uni-mary.edu"])
        self.assertEqual(self._list(academician_type="PhD Scholar",
                                    institute_name="Charlie")["academics"], [])

    def test_sorting_by_each_whitelisted_column(self):
        asc = self._list(sort="institute_name", order="asc")["academics"]
        self.assertEqual([r["institute_name"] for r in asc],
                         ["Alpha University", "Charlie College", "Delta Institute"])
        desc = self._list(sort="institute_name", order="desc")["academics"]
        self.assertEqual([r["institute_name"] for r in desc],
                         ["Delta Institute", "Charlie College", "Alpha University"])
        by_phone = self._list(sort="phone", order="asc")["academics"]
        self.assertEqual([r["phone"] for r in by_phone],
                         ["+91 90000 00001", "+91 90000 00002", "+91 90000 00003"])
        by_type = self._list(sort="academician_type", order="asc")["academics"]
        self.assertEqual(by_type[0]["academician_type"], "PhD Scholar")
        by_email = self._list(sort="academic_email", order="asc")["academics"]
        self.assertEqual([r["academic_email"] for r in by_email],
                         ["adam@uni-adam.edu", "mary@uni-mary.edu", "zoe@uni-zoe.edu"])

    def test_unknown_sort_key_falls_back_instead_of_reaching_sql(self):
        result = self._list(sort="id; DROP TABLE users")
        self.assertEqual(result["sort"], "created_at")
        self.assertEqual(len(result["academics"]), 3)

    def test_total_and_pagination(self):
        first = self._list(limit=2, offset=0, sort="institute_name", order="asc")
        second = self._list(limit=2, offset=2, sort="institute_name", order="asc")
        self.assertEqual(first["total"], second["total"])
        self.assertEqual(first["total"], 3)
        ids = {r["id"] for r in first["academics"]} | {r["id"] for r in second["academics"]}
        self.assertEqual(len(ids), 3)

    def test_verified_filter(self):
        marker = f"{self.MARKER}-VER-{next(_counter)}"
        confirmed = make_academic(unique_email("verified"), institute_id=marker)
        pending = make_academic(unique_email("unverified"), institute_id=marker)
        billing_store.mark_email_verified(confirmed["id"])
        verified_rows = self._list(institute_id=marker, verified=True)["academics"]
        unverified_rows = self._list(institute_id=marker, verified=False)["academics"]
        self.assertEqual([r["email"] for r in verified_rows], [confirmed["email"]])
        self.assertEqual([r["email"] for r in unverified_rows], [pending["email"]])


class AdminApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(engine_app.app)
        # The router reads this at import time, so set it per test.
        self._token = routes_billing.ADMIN_TOKEN
        routes_billing.ADMIN_TOKEN = "admin-test-token"
        self.addCleanup(setattr, routes_billing, "ADMIN_TOKEN", self._token)

    def test_directory_requires_the_admin_token(self):
        self.assertEqual(self.client.get("/api/admin/academics").status_code, 401)
        self.assertEqual(
            self.client.get("/api/admin/academics",
                            headers={"X-Admin-Token": "wrong"}).status_code, 401)

    def test_directory_sorts_and_filters_over_http(self):
        institute = f"Beta Institute {next(_counter)}"
        make_academic(unique_email("api-visitor"), institute_name=institute,
                      academician_type="Lecturer")
        res = self.client.get("/api/admin/academics",
                              params={"institute_name": institute,
                                      "sort": "academician_type", "order": "asc"},
                              headers=ADMIN)
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual([a["institute_name"] for a in body["academics"]], [institute])
        self.assertIn("Professor", body["academician_types"])

    def test_directory_is_disabled_without_a_configured_token(self):
        routes_billing.ADMIN_TOKEN = ""
        self.assertEqual(
            self.client.get("/api/admin/academics", headers=ADMIN).status_code, 404)


class EmailVerificationTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(engine_app.app)
        self.sent = stub_email(self)

    def _register(self, email):
        res = self.client.post("/api/account/register",
                               json={"email": email, "password": PASSWORD})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def _token_for(self, email):
        link = auth.send_verification(billing_store.get_user_by_email(email))
        self.assertTrue(link, "expected a dev link with EMAIL_EXPOSE_LINKS=1")
        return link.split("token=")[1]

    def test_registering_sends_a_confirmation(self):
        body = self._register(unique_email("verify-me"))
        self.assertTrue(body["verification_required"])
        self.assertIn(body["user"]["email"], self.sent)

    def test_registration_can_carry_the_academic_profile(self):
        email = unique_email("signup-profile")
        res = self.client.post("/api/account/register", json={
            "email": email, "password": PASSWORD,
            "profile": {"institute_name": "Signup Institute",
                        "academician_type": "Assistant Professor"},
        })
        self.assertEqual(res.status_code, 200, res.text)
        profile = billing_store.get_profile(res.json()["user"]["id"])
        self.assertEqual(profile["institute_name"], "Signup Institute")
        self.assertEqual(profile["academician_type"], "Assistant Professor")

    def test_confirmation_marks_the_account_verified(self):
        email = unique_email("confirm")
        self._register(email)
        res = self.client.get("/api/account/verify-email",
                              params={"token": self._token_for(email)})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["status"], "verified")
        self.assertTrue(
            billing_store.get_user_by_email(email)["email_verified_at"])

    def test_a_confirmation_token_only_works_once(self):
        email = unique_email("once-verify")
        self._register(email)
        token = self._token_for(email)
        self.assertEqual(
            self.client.get("/api/account/verify-email", params={"token": token}).status_code,
            200)
        self.assertEqual(
            self.client.get("/api/account/verify-email", params={"token": token}).status_code,
            400)

    def test_reissued_link_invalidates_the_previous_one(self):
        email = unique_email("reissue")
        self._register(email)
        first = self._token_for(email)
        second = self._token_for(email)
        self.assertEqual(
            self.client.get("/api/account/verify-email", params={"token": first}).status_code, 400)
        self.assertEqual(
            self.client.get("/api/account/verify-email", params={"token": second}).status_code, 200)

    def test_garbage_tokens_are_refused(self):
        for bad in ("", "nope", "a" * 200):
            self.assertEqual(
                self.client.get("/api/account/verify-email", params={"token": bad}).status_code,
                400)

    def test_verification_can_be_required_before_signing_in(self):
        email = unique_email("gate")
        self._register(email)
        original = auth.REQUIRE_VERIFIED_EMAIL
        auth.REQUIRE_VERIFIED_EMAIL = True
        try:
            res = self.client.post("/api/account/login",
                                   json={"email": email, "password": PASSWORD})
            self.assertEqual(res.status_code, 403)
            self.assertEqual(res.json()["detail"]["code"], "email_unverified")
        finally:
            auth.REQUIRE_VERIFIED_EMAIL = original

    def test_resend_is_generic_and_does_not_leak_accounts(self):
        known = self.client.post("/api/account/resend-verification",
                                 json={"email": unique_email("resend")})
        unknown = self.client.post("/api/account/resend-verification",
                                   json={"email": unique_email("nobody")})
        self.assertEqual(known.status_code, 200)
        self.assertEqual(known.json(), unknown.json())


class PasswordResetTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(engine_app.app)
        self.sent = stub_email(self)

    def _register(self):
        email = unique_email("reset")
        res = self.client.post("/api/account/register",
                               json={"email": email, "password": PASSWORD})
        self.assertEqual(res.status_code, 200, res.text)
        return email

    def _reset_link(self, email):
        res = self.client.post("/api/account/forgot-password", json={"email": email})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["dev_link"].split("token=")[1]

    def test_forgot_password_sends_a_link_for_a_known_account(self):
        email = self._register()
        self.client.post("/api/account/forgot-password", json={"email": email})
        self.assertIn(email, self.sent)

    def test_forgot_password_is_generic_for_unknown_addresses(self):
        known = self.client.post("/api/account/forgot-password",
                                 json={"email": self._register()})
        unknown = self.client.post("/api/account/forgot-password",
                                   json={"email": unique_email("ghost")})
        self.assertEqual(known.json()["status"], unknown.json()["status"])
        self.assertNotIn("dev_link", unknown.json())

    def test_reset_changes_the_password_and_ends_old_sessions(self):
        email = self._register()
        signed_in = TestClient(engine_app.app)
        signed_in.post("/api/account/login", json={"email": email, "password": PASSWORD})
        self.assertIsNotNone(signed_in.get("/api/account/me").json()["user"])

        token = self._reset_link(email)
        res = self.client.post("/api/account/reset-password",
                               json={"token": token, "password": "a-brand-new-pass"})
        self.assertEqual(res.status_code, 200, res.text)

        # The old session is dead and the old password no longer works.
        self.assertIsNone(signed_in.get("/api/account/me").json()["user"])
        self.assertEqual(
            self.client.post("/api/account/login",
                             json={"email": email, "password": PASSWORD}).status_code, 401)
        self.assertEqual(
            self.client.post("/api/account/login",
                             json={"email": email,
                                   "password": "a-brand-new-pass"}).status_code, 200)

    def test_reset_token_is_single_use(self):
        email = self._register()
        token = self._reset_link(email)
        first = self.client.post("/api/account/reset-password",
                                 json={"token": token, "password": "first-new-password"})
        second = self.client.post("/api/account/reset-password",
                                  json={"token": token, "password": "second-new-password"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 400)

    def test_reset_enforces_password_rules(self):
        email = self._register()
        token = self._reset_link(email)
        res = self.client.post("/api/account/reset-password",
                               json={"token": token, "password": "short"})
        self.assertEqual(res.status_code, 400)
        # A rejected attempt must not burn the link.
        self.assertEqual(
            self.client.post("/api/account/reset-password",
                             json={"token": token,
                                   "password": "long-enough-password"}).status_code, 200)

    def test_reset_requests_are_throttled_per_account(self):
        email = self._register()
        original = auth.RESET_REQUESTS_PER_HOUR
        auth.RESET_REQUESTS_PER_HOUR = 2
        self.addCleanup(setattr, auth, "RESET_REQUESTS_PER_HOUR", original)
        self.sent.clear()
        for _ in range(2):
            self.assertEqual(
                self.client.post("/api/account/forgot-password",
                                 json={"email": email}).status_code, 200)
        self.client.post("/api/account/forgot-password", json={"email": email})
        self.assertEqual(len(self.sent), 2, "throttling must stop further sends")

    def test_a_reset_token_cannot_be_used_to_verify_an_email(self):
        email = self._register()
        res = self.client.get("/api/account/verify-email",
                              params={"token": self._reset_link(email)})
        self.assertEqual(res.status_code, 400)


class ProfileApiTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(engine_app.app)
        self.email = unique_email("profile-api")
        res = self.client.post("/api/account/register",
                               json={"email": self.email, "password": PASSWORD})
        self.assertEqual(res.status_code, 200, res.text)

    def test_profile_requires_sign_in(self):
        self.assertEqual(TestClient(engine_app.app).get("/api/account/profile").status_code, 401)

    def test_profile_can_be_saved_and_read_back(self):
        res = self.client.put("/api/account/profile", json={
            "institute_name": "Institute of Testing",
            "institute_id": "IOT-1",
            "academic_email": "someone@iot.edu",
            "phone": "+91 40 1234 5678",
            "academician_type": "Research Scientist",
        })
        self.assertEqual(res.status_code, 200, res.text)
        saved = res.json()["profile"]
        self.assertEqual(saved["institute_id"], "IOT-1")
        self.assertEqual(saved["academician_type"], "Research Scientist")
        self.assertEqual(self.client.get("/api/account/profile").json()["profile"], saved)

    def test_invalid_profile_is_rejected_with_the_allowed_types(self):
        res = self.client.put("/api/account/profile",
                              json={"academician_type": "Warlord"})
        self.assertEqual(res.status_code, 400)
        self.assertIn("Professor", res.json()["detail"]["allowed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
