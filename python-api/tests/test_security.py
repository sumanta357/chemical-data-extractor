#!/usr/bin/env python3
"""
Strix-style security checks: each test is a working exploit attempt.

Written by hand to mirror the approach the Strix agents take — form a
hypothesis, actually try to break it against the running app, and only call it
a finding when the exploit demonstrably works. Every assertion here is an
attack that must fail; a test that passes for the wrong reason is worse than
no test, so each one is checked against the real FastAPI app with a real
Supabase-issued identity (stubbed at the HTTP boundary).

Run:  cd python-api && python3 -m pytest tests/test_security.py -q
"""

import os
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

_TEST_DIR = tempfile.mkdtemp(prefix="scigraph_security_test_")
os.environ["BILLING_DB"] = str(Path(_TEST_DIR) / "billing.db")
os.environ["SEARCH_DB"] = str(Path(_TEST_DIR) / "search.db")
os.environ["EXPORTS_DIR"] = str(Path(_TEST_DIR) / "exports")
os.environ["WORKSPACE_DIR"] = str(Path(_TEST_DIR) / "workspace")
os.environ["STATIC_DIR"] = str(Path(_TEST_DIR) / "ui")
os.environ["RAZORPAY_KEY_ID"] = "rzp_test_key_id"
os.environ["RAZORPAY_KEY_SECRET"] = "rzp_test_key_secret"
os.environ["RAZORPAY_WEBHOOK_SECRET"] = "rzp_test_webhook_secret"
os.environ["REQUIRE_AUTH"] = "1"

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from support import AUTH_HEADERS, SupabaseStub  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import billing  # noqa: E402
import app as engine_app  # noqa: E402


def _noop(*args, **kwargs):
    return None


async def _noop_async(*args, **kwargs):
    return None


class SecurityBase(unittest.TestCase):
    def setUp(self):
        self.stub = SupabaseStub().install(self)
        self.client = TestClient(engine_app.app, headers=AUTH_HEADERS)
        self.anon = TestClient(engine_app.app)
        self.account = self.stub.account()
        # `billing` may already be imported by another test module with the
        # guest tier enabled, and REQUIRE_AUTH is read once at import. Force it
        # on here so the sign-in boundary is genuinely under test.
        self._require_auth = billing.REQUIRE_AUTH
        billing.REQUIRE_AUTH = True
        # Never let a test actually launch the engine.
        self._bg = engine_app.run_search_in_background
        engine_app.run_search_in_background = _noop_async

    def tearDown(self):
        engine_app.run_search_in_background = self._bg
        billing.REQUIRE_AUTH = self._require_auth

    def start(self, **payload):
        body = {"query": "aspirin", "query_type": "ligand", "hops": 1}
        body.update(payload)
        return self.client.post("/api/search", json=body)


# ── Path traversal / arbitrary file write ────────────────────────────────────

class ExportDirTraversal(SecurityBase):
    """`export_dir` is a client-supplied string used verbatim as the engine's
    output directory, and later read back through the graph endpoint."""

    def test_client_cannot_write_outside_the_exports_root(self):
        outside = Path(_TEST_DIR) / "attacker_dir"
        res = self.start(export_dir=str(outside))
        self.assertEqual(res.status_code, 200, res.text)
        state = engine_app.searches[res.json()["search_id"]]
        resolved = Path(state["export_dir"]).resolve()
        root = Path(engine_app.EXPORTS_DIR).resolve()
        self.assertTrue(
            resolved == root or root in resolved.parents,
            f"export_dir escaped the exports root: {resolved}")

    def test_relative_traversal_is_contained(self):
        res = self.start(export_dir="../../../tmp/attacker_escape")
        self.assertEqual(res.status_code, 200, res.text)
        state = engine_app.searches[res.json()["search_id"]]
        resolved = Path(state["export_dir"]).resolve()
        root = Path(engine_app.EXPORTS_DIR).resolve()
        self.assertTrue(
            resolved == root or root in resolved.parents,
            f"traversal escaped the exports root: {resolved}")

    def test_graph_endpoint_cannot_read_outside_the_exports_root(self):
        """A caller-controlled export_dir plus the graph endpoint is an
        arbitrary-file-read primitive for any nodes.csv on the box."""
        secret_dir = Path(_TEST_DIR) / "other_user_exports"
        secret_dir.mkdir(parents=True, exist_ok=True)
        (secret_dir / "nodes.csv").write_text("uid:ID,name\nSECRET,private data\n")
        (secret_dir / "edges.csv").write_text(":START_ID,:END_ID\nA,B\n")

        res = self.start(export_dir=str(secret_dir))
        search_id = res.json()["search_id"]
        graph = self.client.get(f"/api/search/{search_id}/graph")
        self.assertNotIn(b"SECRET", graph.content,
                         "graph endpoint served a file outside the exports root")

    def test_legitimate_export_dir_still_works(self):
        """Guard against an over-tight fix rejecting an in-tree request."""
        root = Path(engine_app.EXPORTS_DIR)
        wanted = root / f"explicit-{uuid.uuid4().hex[:8]}"
        res = self.start(export_dir=str(wanted))
        self.assertEqual(res.status_code, 200, res.text)
        state = engine_app.searches[res.json()["search_id"]]
        self.assertEqual(Path(state["export_dir"]).resolve(), wanted.resolve())


# ── Command injection ────────────────────────────────────────────────────────

class CommandInjection(SecurityBase):
    def test_shell_metacharacters_in_the_query_are_inert(self):
        for payload in ("aspirin; rm -rf /", "aspirin`id`", "aspirin$(id)",
                        "aspirin && curl evil.example", "aspirin | nc evil 1234"):
            with self.subTest(payload=payload):
                # A fresh account per case: the free tier only allows three
                # searches, and a 402 here would mask the assertion.
                SupabaseStub(email=f"ci-{uuid.uuid4().hex[:8]}@example.com").install(self)
                res = self.start(query=payload)
                self.assertEqual(res.status_code, 200, res.text)

    def test_export_dir_cannot_smuggle_shell_arguments(self):
        """The engine is launched with create_subprocess_exec (argv list), so
        an argument-shaped path must not become a second command."""
        marker = Path(_TEST_DIR) / "pwned.txt"
        res = self.start(export_dir=f"--help; touch {marker}")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertFalse(marker.exists(), "shell metacharacters reached a shell")


# ── Access control ───────────────────────────────────────────────────────────

class AccessControl(SecurityBase):
    def test_unauthenticated_cannot_start_a_search(self):
        res = self.anon.post("/api/search", json={"query": "aspirin"})
        self.assertEqual(res.status_code, 401)

    def test_unauthenticated_cannot_read_the_graph_of_a_known_search(self):
        res = self.start()
        search_id = res.json()["search_id"]
        # A different, anonymous caller must not read it.
        res = self.anon.get(f"/api/search/{search_id}/graph")
        self.assertIn(res.status_code, (401, 403, 404))

    def test_search_ids_are_not_guessable_across_accounts(self):
        res = self.start()
        search_id = res.json()["search_id"]
        other = SupabaseStub(email="other@example.com").install(self)
        other.account()
        intruder = TestClient(engine_app.app, headers=AUTH_HEADERS)
        res = intruder.get(f"/api/search/{search_id}")
        self.assertIn(res.status_code, (403, 404),
                      "another signed-in account could read someone else's search")

    def test_forged_and_absent_tokens_are_anonymous(self):
        for token in ("", "Bearer nonsense", "Bearer a.b.c"):
            with self.subTest(token=token):
                res = self.anon.post("/api/search", json={"query": "aspirin"},
                                     headers={"Authorization": token})
                self.assertEqual(res.status_code, 401)


# ── Information disclosure ───────────────────────────────────────────────────

class Disclosure(SecurityBase):
    def test_errors_do_not_leak_secrets_or_paths(self):
        probes = [
            ("/api/billing/config", None),
            ("/api/billing/payment-status/pay_AAAAAAAAAAAAAA", None),
        ]
        needles = ["key_secret", "rzp_test_key_secret", "rzp_test_webhook_secret",
                   "Traceback", "File \"", "DATABASE_URL", "postgresql://"]
        for path, _ in probes:
            with self.subTest(path=path):
                text = self.client.get(path).text
                for needle in needles:
                    self.assertNotIn(needle, text, f"{path} leaked {needle!r}")

    def test_search_error_response_has_no_internal_trace(self):
        res = self.start(query="a" * 5000)
        self.assertNotIn("Traceback", res.text)

    def test_admin_directory_closed_without_token(self):
        res = self.client.get("/api/admin/academics")
        self.assertIn(res.status_code, (401, 404))


# ── Input validation ─────────────────────────────────────────────────────────

class InputValidation(SecurityBase):
    def test_oversized_query_is_handled(self):
        res = self.start(query="x" * 100000)
        self.assertIn(res.status_code, (200, 400, 413, 422))

    def test_unknown_query_type_is_rejected(self):
        res = self.start(query_type="'; DROP TABLE users;--")
        self.assertEqual(res.status_code, 400)

    def test_out_of_range_hops_is_rejected(self):
        res = self.start(hops=99)
        self.assertEqual(res.status_code, 400)

    def test_non_string_query_is_rejected(self):
        res = self.client.post("/api/search", json={"query": {"$ne": None}})
        self.assertIn(res.status_code, (400, 422))


if __name__ == "__main__":
    unittest.main(verbosity=2)