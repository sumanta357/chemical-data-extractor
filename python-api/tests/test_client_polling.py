#!/usr/bin/env python3
"""
The browser polls the engine without credentials.

Since searches became owner-scoped, `GET /api/search/{id}` answers 404 to any
caller it cannot attribute to the search's owner. The page polled it with a
plain `fetch`, so the first poll of every signed-in user's search came back 404
and the UI turned that into "The search engine restarted while this search was
running" — a memory/deploy diagnosis for what is actually a missing header.

These tests pin both halves: the server really does 404 an unattributed
caller (so the header is genuinely required), and the page really does send it.

Run:  cd python-api && python3 -m pytest tests/test_client_polling.py -q
"""

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

_TEST_DIR = tempfile.mkdtemp(prefix="scigraph_polling_test_")
os.environ["BILLING_DB"] = str(Path(_TEST_DIR) / "billing.db")
os.environ["SEARCH_DB"] = str(Path(_TEST_DIR) / "search.db")
os.environ["EXPORTS_DIR"] = str(Path(_TEST_DIR) / "exports")
os.environ["WORKSPACE_DIR"] = str(Path(_TEST_DIR) / "workspace")
os.environ["STATIC_DIR"] = str(Path(_TEST_DIR) / "ui")
os.environ["REQUIRE_AUTH"] = "1"

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from support import AUTH_HEADERS, SupabaseStub  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import billing  # noqa: E402
import app as engine_app  # noqa: E402


async def _noop_async(*args, **kwargs):
    return None


class PollingWithoutCredentials(unittest.TestCase):
    def setUp(self):
        self.stub = SupabaseStub().install(self)
        self.account = self.stub.account()
        self.client = TestClient(engine_app.app, headers=AUTH_HEADERS)
        # The browser's poll carries no Authorization header at all.
        self.anon = TestClient(engine_app.app)
        self._require_auth = billing.REQUIRE_AUTH
        billing.REQUIRE_AUTH = True
        self._bg = engine_app.run_search_in_background
        engine_app.run_search_in_background = _noop_async

    def tearDown(self):
        engine_app.run_search_in_background = self._bg
        billing.REQUIRE_AUTH = self._require_auth

    def _start(self):
        res = self.client.post(
            "/api/search",
            json={"query": "aspirin", "query_type": "ligand", "hops": 1})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()["search_id"]

    def test_the_search_is_owned_so_the_header_is_actually_required(self):
        """If the search carried no owner this whole file would be testing
        nothing — pin that the account really is attached to it."""
        search_id = self._start()
        state = engine_app.searches[search_id]
        self.assertIsNotNone(state.get("owner_user_id"),
                             "search was not attributed to an account")

    def test_poll_without_a_token_gets_404_which_the_page_calls_a_restart(self):
        search_id = self._start()
        res = self.anon.get(f"/api/search/{search_id}")
        self.assertEqual(
            res.status_code, 404,
            "an unauthenticated poll is no longer rejected, so this regression "
            "guard no longer describes the live behaviour")

    def test_the_owners_poll_still_works(self):
        """Guard against an over-tight fix that locks the owner out too."""
        search_id = self._start()
        self.assertEqual(self.client.get(f"/api/search/{search_id}").status_code, 200)


class PageSendsCredentials(unittest.TestCase):
    """The client half. A 404 from the status endpoint is turned into the
    "engine restarted" message, so every fetch of an owner-scoped endpoint in
    the page must carry the Supabase token — otherwise a restart, a spin-down
    and a missing header all look identical to the user."""

    PAGE = _REPO_ROOT / "app" / "page.tsx"
    HISTORY = _REPO_ROOT / "components" / "HistoryPanel.tsx"

    # Every read that the server scopes to the caller's account:
    #   GET /api/search/{id}          (the poll and the history click-through)
    #   GET /api/search/{id}/log      (streamed engine output)
    #   GET /api/searches             (the history list)
    PATTERN = re.compile(
        r"fetch\(\s*(?:`/api/search/\$\{[^}]+\}[^`]*`|'/api/searches\?[^']*')",
        re.MULTILINE,
    )

    def _owner_scoped_fetches(self):
        found = []
        for path in (self.PAGE, self.HISTORY):
            source = path.read_text()
            found += [(path.name, m.start(), source) for m in self.PATTERN.finditer(source)]
        return found

    def test_every_owner_scoped_fetch_carries_the_token(self):
        found = self._owner_scoped_fetches()
        self.assertGreaterEqual(
            len(found), 3,
            f"expected the poll, the log stream and the history list; found {len(found)}")
        for name, start, source in found:
            call = source[start:start + 400]
            self.assertIn(
                "authHeaders()", call,
                f"{name}: an owner-scoped fetch has no authHeaders(): {call[:120]!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)