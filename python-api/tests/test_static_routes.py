#!/usr/bin/env python3
"""
Static-export route resolution.

The UI is built with `output: 'export'`, so every route becomes
<route>/index.html on disk. The SPA handler must serve those directories
(Admin directory, reset password, verify email) instead of falling through to
the application shell, while unknown client routes still get the shell.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

_TEST_DIR = tempfile.mkdtemp(prefix="scigraph_static_test_")
os.environ.setdefault("BILLING_DB", str(Path(_TEST_DIR) / "billing.db"))
os.environ.setdefault("SEARCH_DB", str(Path(_TEST_DIR) / "search.db"))

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# A miniature static export with the same <route>/index.html layout.
STATIC = Path(_TEST_DIR) / "ui-out"
(STATIC / "admin" / "academics").mkdir(parents=True)
(STATIC / "reset-password").mkdir(parents=True)
(STATIC / "_next").mkdir(parents=True)
(STATIC / "index.html").write_text("<html>APP_SHELL</html>", encoding="utf-8")
(STATIC / "admin" / "academics" / "index.html").write_text(
    "<html>ACADEMIC_DIRECTORY</html>", encoding="utf-8")
(STATIC / "reset-password" / "index.html").write_text(
    "<html>RESET_PASSWORD</html>", encoding="utf-8")
os.environ["STATIC_DIR"] = str(STATIC)

from fastapi.testclient import TestClient  # noqa: E402

import app as engine_app  # noqa: E402

# app.py reads STATIC_DIR at import time and another test module may already
# have imported it, so point the handler at our miniature export explicitly.
engine_app.STATIC_DIR = STATIC


class StaticRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(engine_app.app)

    def test_directory_route_is_served_not_the_shell(self):
        res = self.client.get("/admin/academics")
        self.assertEqual(res.status_code, 200)
        self.assertIn("ACADEMIC_DIRECTORY", res.text)
        self.assertNotIn("APP_SHELL", res.text)

    def test_directory_route_works_with_a_trailing_slash(self):
        res = self.client.get("/admin/academics/")
        self.assertEqual(res.status_code, 200)
        self.assertIn("ACADEMIC_DIRECTORY", res.text)

    def test_reset_password_route_is_served(self):
        res = self.client.get("/reset-password?token=abc")
        self.assertEqual(res.status_code, 200)
        self.assertIn("RESET_PASSWORD", res.text)

    def test_unknown_client_route_still_gets_the_shell(self):
        res = self.client.get("/some/client/route")
        self.assertEqual(res.status_code, 200)
        self.assertIn("APP_SHELL", res.text)

    def test_api_routes_are_not_swallowed_by_the_static_handler(self):
        body = self.client.get("/api/billing/config").json()
        self.assertIn("key_id", body)
        self.assertEqual(self.client.get("/api/account/me").status_code, 200)

    def test_path_traversal_is_refused(self):
        res = self.client.get("/../../etc/passwd")
        self.assertNotIn("root:", res.text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
