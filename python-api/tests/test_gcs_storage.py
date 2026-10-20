"""Durable export storage in Google Cloud Storage.

These tests never touch a real bucket. The client is a stand-in that records
what it was asked to do, so the questions here are the ones a bucket would
answer badly: does a search that finished still report success when storage is
misconfigured, does an unconfigured deployment behave exactly as before, and can
two users' archives ever be confused for one another.
"""

import io
import json
import os
import sys
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gcs_storage


class FakeBlob:
    def __init__(self, bucket, name, store):
        self._bucket = bucket.name
        self.name = name
        self._store = store
        self.uploaded = None

    def upload_from_string(self, raw, content_type=None, generation=None):
        self._store.append({"bucket": self._bucket, "name": self.name, "raw": raw,
                            "content_type": content_type, "generation": generation})
        self.uploaded = raw

    def generate_signed_url(self, version=None, expiration=None, method=None):
        return f"https://storage.googleapis.com/{self._bucket}/{self.name}?X-Goog-Signature=stub"

    def delete(self):
        self._store.append({"deleted": self.name})


class FakeBucket:
    def __init__(self, name, store):
        self.name = name
        self._store = store

    def blob(self, name):
        return FakeBlob(self, name, self._store)


class FakeClient:
    """Stands in for google.cloud.storage.Client."""

    def __init__(self):
        self.store = []

    def bucket(self, name):
        return FakeBucket(name, self.store)


def _env(**overrides):
    """A clean environment: storage off unless a test turns it on."""
    base = {
        "GCS_BUCKET": "",
        "GOOGLE_APPLICATION_CREDENTIALS_JSON": "",
        "GOOGLE_APPLICATION_CREDENTIALS": "",
        "GCS_MAX_ARCHIVE_MB": "200",
        "GCS_SIGNED_URL_TTL_SECONDS": "604800",
    }
    base.update(overrides)
    return mock.patch.dict(os.environ, base, clear=False)


SERVICE_ACCOUNT = json.dumps({
    "type": "service_account",
    "project_id": "example",
    "client_email": "archiver@example.iam.gserviceaccount.com",
    "private_key": "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n",
})


def _export_dir(root: Path, files=None) -> str:
    d = root / "exports"
    d.mkdir(parents=True, exist_ok=True)
    for name, body in (files or {
        "nodes.csv": "uid,type\n1,compound\n",
        "edges.csv": "src,dst\n1,2\n",
        "enriched_data.xlsx": "PK\x03\x04fake",
    }).items():
        (d / name).write_text(body, encoding="utf-8")
    return str(d)


class ArchiveContents(unittest.TestCase):
    """The archive is the user's whole result set, so nothing may be dropped."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_archives_every_export_file(self):
        export_dir = _export_dir(self.root)
        raw = gcs_storage.build_archive(export_dir, "x.zip")
        self.assertIsNotNone(raw)
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            names = set(zf.namelist())
            self.assertEqual(names, {"nodes.csv", "edges.csv", "enriched_data.xlsx"})
            # Contents survive the round trip, not just the filenames.
            self.assertEqual(zf.read("nodes.csv"), b"uid,type\n1,compound\n")

    def test_includes_nested_structure_images(self):
        export_dir = _export_dir(self.root)
        images = Path(export_dir) / "structure_images"
        images.mkdir()
        (images / "aspirin.png").write_bytes(b"\x89PNG fake")
        with zipfile.ZipFile(io.BytesIO(gcs_storage.build_archive(export_dir, "x.zip"))) as zf:
            self.assertIn("structure_images/aspirin.png", zf.namelist())

    def test_skips_dotfiles(self):
        export_dir = _export_dir(self.root)
        Path(export_dir, ".partial").write_text("half written", encoding="utf-8")
        with zipfile.ZipFile(io.BytesIO(gcs_storage.build_archive(export_dir, "x.zip"))) as zf:
            self.assertNotIn(".partial", zf.namelist())

    def test_empty_directory_yields_nothing(self):
        (self.root / "empty").mkdir()
        self.assertIsNone(gcs_storage.build_archive(str(self.root / "empty"), "x.zip"))

    def test_missing_directory_yields_nothing(self):
        self.assertIsNone(gcs_storage.build_archive(str(self.root / "gone"), "x.zip"))

    def test_oversized_archive_is_refused_not_truncated(self):
        # A partial archive that looks complete would be worse than none.
        # The limit is read at import, like EXPORT_MAX_AGE_MINUTES in app.py,
        # so it is patched on the module rather than through the environment.
        export_dir = _export_dir(self.root, {"big.csv": "x" * 4096})
        with mock.patch.object(gcs_storage, "MAX_ARCHIVE_MB", 0):
            self.assertIsNone(gcs_storage.build_archive(export_dir, "x.zip"))


class DisabledByDefault(unittest.TestCase):
    """An unconfigured deployment must behave exactly as it did before."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.export_dir = _export_dir(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_not_configured_without_env(self):
        with _env():
            self.assertFalse(gcs_storage.is_configured())

    def test_not_configured_without_bucket(self):
        with _env(GOOGLE_APPLICATION_CREDENTIALS_JSON=SERVICE_ACCOUNT):
            self.assertFalse(gcs_storage.is_configured())

    def test_not_configured_without_credentials(self):
        with _env(GCS_BUCKET="my-bucket"):
            self.assertFalse(gcs_storage.is_configured())

    def test_malformed_credentials_json_is_not_configured(self):
        with _env(GCS_BUCKET="my-bucket", GOOGLE_APPLICATION_CREDENTIALS_JSON="{not json"):
            self.assertFalse(gcs_storage.is_configured())

    def test_json_without_client_email_is_not_configured(self):
        with _env(GCS_BUCKET="my-bucket", GOOGLE_APPLICATION_CREDENTIALS_JSON='{"a":1}'):
            self.assertFalse(gcs_storage.is_configured())

    def test_upload_is_a_noop_when_disabled(self):
        with _env(), mock.patch.object(gcs_storage, "client", return_value=None):
            self.assertIsNone(
                gcs_storage.upload_search_exports(1, "abc123", "EGFR", self.export_dir)
            )

    def test_signed_url_is_none_when_disabled(self):
        with _env(), mock.patch.object(gcs_storage, "client", return_value=None):
            self.assertIsNone(gcs_storage.signed_url("exports/1/abc/a.zip"))


class Enabled(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.export_dir = _export_dir(self.root)
        self.enabled = _env(GCS_BUCKET="my-bucket",
                            GOOGLE_APPLICATION_CREDENTIALS_JSON=SERVICE_ACCOUNT)

    def tearDown(self):
        self.tmp.cleanup()

    def test_is_configured_with_bucket_and_credentials(self):
        with self.enabled:
            self.assertTrue(gcs_storage.is_configured())

    def test_upload_stores_the_archive_and_returns_metadata(self):
        fake = FakeClient()
        with self.enabled, mock.patch.object(gcs_storage, "client", return_value=fake):
            result = gcs_storage.upload_search_exports(7, "abc123", "EGFR inhibitors", self.export_dir)
        self.assertIsNotNone(result)
        self.assertEqual(result["provider"], "gcs")
        self.assertEqual(result["bucket"], "my-bucket")
        self.assertGreater(result["size_bytes"], 0)
        self.assertEqual(len(fake.store), 1)
        self.assertEqual(fake.store[0]["content_type"], "application/zip")
        # generation=0 makes the object immutable, so a link handed out for one
        # search can never later serve different bytes.
        self.assertEqual(fake.store[0]["generation"], 0)
        with zipfile.ZipFile(io.BytesIO(fake.store[0]["raw"])) as zf:
            self.assertIn("nodes.csv", zf.namelist())

    def test_object_name_is_scoped_to_the_owner(self):
        with self.enabled:
            name = gcs_storage.object_name(42, "abc123", "run.zip")
        self.assertEqual(name, "exports/42/abc123/run.zip")

    def test_anonymous_searches_get_their_own_prefix(self):
        with self.enabled:
            self.assertEqual(gcs_storage.object_name(None, "abc123", "r.zip"),
                             "exports/anonymous/abc123/r.zip")

    def test_two_users_never_collide_on_the_same_object(self):
        with self.enabled:
            first = gcs_storage.object_name(1, "s1", "r.zip")
            second = gcs_storage.object_name(2, "s1", "r.zip")
        self.assertNotEqual(first, second)

    def test_two_archives_of_one_search_get_distinct_names(self):
        # Same search, same second: the random suffix is what keeps them apart.
        with self.enabled:
            self.assertNotEqual(gcs_storage.archive_name("s1", "EGFR"),
                                gcs_storage.archive_name("s1", "EGFR"))

    def test_different_searches_get_distinct_names(self):
        with self.enabled:
            self.assertNotEqual(gcs_storage.archive_name("s1", "EGFR"),
                                gcs_storage.archive_name("s2", "EGFR"))

    def test_object_name_rejects_path_traversal(self):
        # Both the query and the search id reach the object name, so neither
        # may be able to forge a directory or climb out of its prefix.
        with self.enabled:
            name = gcs_storage.archive_name("../../etc", "../../passwd")
        self.assertNotIn("/", name)
        self.assertNotIn("..", name)
        self.assertTrue(name.endswith(".zip"))

    def test_signed_url_points_at_the_stored_object(self):
        fake = FakeClient()
        with self.enabled, mock.patch.object(gcs_storage, "client", return_value=fake):
            url = gcs_storage.signed_url("exports/7/abc123/run.zip")
        self.assertIn("my-bucket", url)
        self.assertIn("exports/7/abc123/run.zip", url)


class NeverBreaksASearch(unittest.TestCase):
    """The whole point: storage trouble must not fail a finished search."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.export_dir = _export_dir(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_upload_failure_returns_none_and_does_not_raise(self):
        class Exploding(FakeClient):
            def bucket(self, name):
                raise RuntimeError("bucket is on fire")

        with _env(GCS_BUCKET="my-bucket",
                   GOOGLE_APPLICATION_CREDENTIALS_JSON=SERVICE_ACCOUNT), \
                mock.patch.object(gcs_storage, "client", return_value=Exploding()):
            self.assertIsNone(
                gcs_storage.upload_search_exports(1, "abc123", "EGFR", self.export_dir)
            )

    def test_signed_url_failure_returns_none_and_does_not_raise(self):
        class Exploding(FakeClient):
            def bucket(self, name):
                raise RuntimeError("bucket is on fire")

        with _env(GCS_BUCKET="my-bucket",
                   GOOGLE_APPLICATION_CREDENTIALS_JSON=SERVICE_ACCOUNT), \
                mock.patch.object(gcs_storage, "client", return_value=Exploding()):
            self.assertIsNone(gcs_storage.signed_url("exports/1/a/r.zip"))

    def test_client_build_failure_is_swallowed(self):
        with _env(GCS_BUCKET="my-bucket",
                   GOOGLE_APPLICATION_CREDENTIALS_JSON=SERVICE_ACCOUNT), \
                mock.patch.dict(sys.modules, {"google.cloud": None}), \
                mock.patch.object(gcs_storage, "_client", None):
            self.assertIsNone(gcs_storage.client())


if __name__ == "__main__":
    unittest.main()
