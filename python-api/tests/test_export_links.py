#!/usr/bin/env python3
"""Signed export links: the capability URL Cytoscape opens a search with.

Cytoscape imports a network from a URL, but it sends no session cookie, so the
graph has to be reachable through a signed, expiring link. These tests mint one
as the search's owner, fetch it with no credentials at all, and prove that a
tampered, expired or other-file link is refused.

Run: cd python-api && python3 -m pytest tests/test_export_links.py -q
"""

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, urlparse

_TEST_DIR = tempfile.mkdtemp(prefix="scigraph_export_link_test_")
os.environ["BILLING_DB"] = str(Path(_TEST_DIR) / "billing.db")
os.environ["SEARCH_DB"] = str(Path(_TEST_DIR) / "search.db")
os.environ["EXPORTS_DIR"] = str(Path(_TEST_DIR) / "exports")
os.environ["WORKSPACE_DIR"] = str(Path(_TEST_DIR) / "workspace")
os.environ["REQUIRE_AUTH"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as engine_app  # noqa: E402
from support import AUTH_HEADERS, SupabaseStub  # noqa: E402

GRAPHML = b'<?xml version="1.0"?><graphml/>'
CYJS = b'{"elements":{"nodes":[],"edges":[]}}'


class ExportLinks(unittest.TestCase):
    def setUp(self):
        search_dir = Path(engine_app.EXPORTS_DIR) / "lnk00001"
        search_dir.mkdir(parents=True, exist_ok=True)
        (search_dir / "graph.graphml").write_bytes(GRAPHML)
        (search_dir / "graph.cyjs").write_bytes(CYJS)
        self.search_dir = str(search_dir)

        self.tmp = TemporaryDirectory()
        self._orig_db = engine_app.SEARCH_DB
        engine_app.SEARCH_DB = Path(self.tmp.name) / "searches.db"
        engine_app._init_db()

        self.stub = SupabaseStub().install(self)
        self.account = self.stub.account()
        self.client = TestClient(engine_app.app, headers=AUTH_HEADERS)

        engine_app.searches.clear()
        engine_app.searches["lnk00001"] = {
            "search_id": "lnk00001",
            "query": "Aspirin",
            "hops": 1,
            "status": "completed",
            "export_dir": self.search_dir,
            "export_files": [],
            "owner_user_id": self.account["id"],
        }

    def tearDown(self):
        engine_app.searches.clear()
        engine_app.SEARCH_DB = self._orig_db
        self.tmp.cleanup()

    def _mint(self, filename="graph.graphml", **params):
        query = {"file": filename, **params}
        return self.client.get("/api/search/lnk00001/export-link", params=query)

    def test_owner_gets_an_absolute_expiring_link(self):
        res = self._mint()
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["file"], "graph.graphml")
        self.assertTrue(body["url"].startswith("http://testserver/api/exports/"))
        self.assertGreater(body["expires_at"], int(time.time()))
        self.assertGreater(body["expires_in"], 0)

        parsed = urlparse(body["url"])
        query = parse_qs(parsed.query)
        self.assertEqual(query["search_id"], ["lnk00001"])
        self.assertTrue(query["signature"][0])

    def test_signed_link_works_without_any_credentials(self):
        url = self._mint().json()["url"]
        parsed = urlparse(url)
        anon = TestClient(engine_app.app)
        res = anon.get(f"{parsed.path}?{parsed.query}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, GRAPHML)

    def test_the_link_is_per_file(self):
        url = self._mint("graph.graphml").json()["url"]
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        anon = TestClient(engine_app.app)
        # Re-using a graphml signature for the cyjs file must not work.
        res = anon.get(
            "/api/exports/graph.cyjs",
            params={
                "search_id": query["search_id"][0],
                "expires": query["expires"][0],
                "signature": query["signature"][0],
            },
        )
        self.assertEqual(res.status_code, 403)

    def test_tampered_signature_is_refused(self):
        url = urlparse(self._mint().json()["url"])
        query = parse_qs(url.query)
        anon = TestClient(engine_app.app)
        res = anon.get(
            url.path,
            params={
                "search_id": query["search_id"][0],
                "expires": query["expires"][0],
                "signature": "0" * 64,
            },
        )
        self.assertEqual(res.status_code, 403)

    def test_expired_link_is_refused_even_with_a_valid_signature(self):
        expires_at = int(time.time()) - 5
        signature = engine_app._export_link_signature(
            "lnk00001", "graph.graphml", expires_at
        )
        anon = TestClient(engine_app.app)
        res = anon.get(
            "/api/exports/graph.graphml",
            params={
                "search_id": "lnk00001",
                "expires": expires_at,
                "signature": signature,
            },
        )
        self.assertEqual(res.status_code, 403)

    def test_unsigned_download_still_works(self):
        """The owner's own browser download must not regress."""
        res = self.client.get(
            "/api/exports/graph.graphml", params={"search_id": "lnk00001"}
        )
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, GRAPHML)

    def test_a_signed_out_caller_with_only_a_search_id_is_refused(self):
        """Search ids are eight characters: knowing one is not ownership."""
        anon = TestClient(engine_app.app)
        res = anon.get("/api/exports/graph.graphml", params={"search_id": "lnk00001"})
        self.assertEqual(res.status_code, 404)
        self.assertNotEqual(res.content, GRAPHML)

    def test_a_download_without_a_search_id_is_refused(self):
        """No search id means no owner to check, so there is no way in."""
        res = self.client.get("/api/exports/graph.graphml")
        self.assertEqual(res.status_code, 403)

    def test_a_scoped_download_cannot_reach_another_searchs_file(self):
        other = Path(engine_app.EXPORTS_DIR) / "other001"
        other.mkdir(parents=True, exist_ok=True)
        (other / "secret.graphml").write_bytes(b"<other/>")
        res = self.client.get(
            "/api/exports/secret.graphml", params={"search_id": "lnk00001"}
        )
        self.assertEqual(res.status_code, 404)

    def test_only_the_owner_can_mint_a_link(self):
        anon = TestClient(engine_app.app)
        res = anon.get(
            "/api/search/lnk00001/export-link", params={"file": "graph.graphml"}
        )
        self.assertEqual(res.status_code, 404)

    def test_a_file_the_search_does_not_have_is_not_linked(self):
        res = self._mint("missing.graphml")
        self.assertEqual(res.status_code, 404)

    def test_base_url_must_be_a_bare_origin(self):
        res = self._mint(base_url="https://app.example.com/with/a/path")
        self.assertEqual(res.status_code, 400)

    def test_base_url_overrides_the_request_origin(self):
        body = self._mint(base_url="https://app.example.com").json()
        self.assertTrue(body["url"].startswith("https://app.example.com/api/exports/"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
