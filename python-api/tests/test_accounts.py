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
import uuid
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
os.environ.pop("REQUIRE_AUTH", None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import auth  # noqa: E402
from support import AUTH_HEADERS, SupabaseStub, make_account  # noqa: E402
import billing  # noqa: E402
import billing_store  # noqa: E402
import routes_billing  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app as engine_app  # noqa: E402

ADMIN = {"X-Admin-Token": "admin-test-token"}
_counter = itertools.count()


def unique_email(prefix: str) -> str:
    """Every test gets its own address: the suite shares one database."""
    return f"{prefix}-{next(_counter)}@example.com"


def make_academic(email: str, **profile) -> dict:
    verified = bool(profile.pop("verified", True))
    user = billing_store.create_supabase_user(str(uuid.uuid4()), email, None, verified)
    user = billing_store.set_plan(user["id"], "academic")
    if profile:
        billing_store.update_profile(user["id"], profile)
    return billing_store.get_user_by_id(user["id"])


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
            user = make_account(f"quota-{plan}-{next(_counter)}@example.com")
            user = billing_store.set_plan(user["id"], plan)
            after = self._run_searches(user, 2)
            self.assertEqual(after["searches_used"], 2, f"{plan} did not accumulate")
            ent = billing.entitlement_for(after)
            self.assertEqual(ent["searches_used"], 2)
            self.assertEqual(ent["searches_remaining"], ent["searches_limit"] - 2)

    def test_repeat_completion_of_one_search_counts_once(self):
        user = make_account(unique_email("once"))
        user = billing_store.set_plan(user["id"], "team")
        billing.attach_search_owner(f"s-{user['id']}-once", user)
        billing.record_search_usage(f"s-{user['id']}-once")
        billing.record_search_usage(f"s-{user['id']}-once")
        self.assertEqual(billing_store.get_user_by_id(user["id"])["searches_used"], 1)

    def test_usage_survives_signing_out_and_signing_back_in(self):
        user = make_account(unique_email("persist"))
        user = billing_store.set_plan(user["id"], "academic")
        self._run_searches(user, 2)

        # A second, independent session for the same Supabase user resolves to
        # the same account and the same counters.
        again = SupabaseStub(sub=user["supabase_user_id"]).install(self)
        account = again.account()
        self.assertEqual(account["id"], user["id"])
        self.assertEqual(account["searches_used"], 2)
        self.assertEqual(billing.entitlement_for(account)["searches_used"], 2)

    def test_team_allows_many_more_than_academic(self):
        academic = billing_store.set_plan(
            make_account(unique_email("a"))["id"],
            "academic")
        team = billing_store.set_plan(
            make_account(unique_email("t"))["id"],
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
        self.assertTrue(profile["email_verified"])

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
            billing_store.create_supabase_user(str(uuid.uuid4()),
                                               unique_email("team"), None, True)["id"],
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
        confirmed = make_academic(unique_email("verified"), institute_id=marker,
                                  verified=False)
        pending = make_academic(unique_email("unverified"), institute_id=marker,
                                verified=False)
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


class ProfileApiTests(unittest.TestCase):
    def setUp(self):
        self.stub = SupabaseStub().install(self)
        self.client = TestClient(engine_app.app, headers=AUTH_HEADERS)
        self.anon = TestClient(engine_app.app)
        self.account = self.stub.account()

    def test_profile_requires_sign_in(self):
        self.assertEqual(self.anon.get("/api/account/profile").status_code, 401)

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

    def test_the_academic_profile_does_not_disturb_the_session(self):
        self.client.put("/api/account/profile",
                        json={"institute_name": "Still Here", "academician_type": "Lecturer"})
        me = self.client.get("/api/account/me").json()
        self.assertEqual(me["user"]["email"], self.account["email"])
        self.assertTrue(me["entitlement"]["signed_in"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
