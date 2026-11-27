#!/usr/bin/env python3
"""
Guards against credentials being committed to the repository.

The AI analyst's provider keys used to sit in `app.py` (and in
`app/api/analyze/route.ts`) as literal fallbacks, so a fresh clone could spend
them. Removing them from HEAD does not revoke them — they are in the public
history and must be rotated at each provider — but this test makes sure a
replacement key never lands in the tree again.

Run:  cd python-api && python3 -m pytest tests/test_secrets.py -q
"""

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]

# Everything a key could plausibly be pasted into. Tests are excluded: they
# hold fake credentials by design and a guard that cries wolf gets deleted.
SCANNED_DIRS = ("api", "app", "components", "lib", "python-api", "scripts")
SKIPPED_DIRS = {
    "tests", "node_modules", ".venv", "__pycache__", ".next", "out",
    ".git", "exports", "ui-out",
}
SCANNED_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".sh", ".yaml", ".yml", ".md"}

# Assembled from pieces so this file never matches its own patterns.
_SECRET_PATTERNS = (
    # OpenAI-compatible bearer keys: sk-…, sk-proj-…
    ("sk-", re.compile(r"\bsk" + r"-(?:[A-Za-z0-9_-]{24,})")),
    # Google AI Studio keys: AQ.Ab8…
    ("AQ.", re.compile(r"\bAQ" + r"\.[A-Za-z0-9_-]{24,}")),
)


def _source_files():
    for directory in SCANNED_DIRS:
        root = REPO_ROOT / directory
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix not in SCANNED_SUFFIXES:
                continue
            if SKIPPED_DIRS & set(path.parts):
                continue
            yield path


class NoCommittedSecretsTest(unittest.TestCase):
    def test_source_tree_contains_no_api_keys(self):
        offenders = []
        for path in _source_files():
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                for label, pattern in _SECRET_PATTERNS:
                    if pattern.search(line):
                        offenders.append(
                            f"{path.relative_to(REPO_ROOT)}:{lineno} ({label}…)"
                        )
        self.assertEqual(
            [],
            offenders,
            "A credential is committed to the repository. Remove it from the "
            "code, rotate it at the provider, and keep the new value in the "
            "deployment environment only:\n  " + "\n  ".join(offenders),
        )


class AnalystProviderConfigTest(unittest.TestCase):
    """Keys are read from the environment, and only from there."""

    @classmethod
    def setUpClass(cls):
        workdir = tempfile.mkdtemp(prefix="scigraph_secrets_test_")
        os.environ.setdefault("BILLING_DB", str(Path(workdir) / "billing.db"))
        os.environ.setdefault("SEARCH_DB", str(Path(workdir) / "search.db"))
        os.environ.setdefault("EXPORTS_DIR", str(Path(workdir) / "exports"))
        os.environ.setdefault("WORKSPACE_DIR", str(Path(workdir) / "workspace"))
        os.environ.setdefault("STATIC_DIR", str(Path(workdir) / "ui"))
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        import app as engine_app

        cls.engine_app = engine_app

    def _providers_with(self, **env):
        blank = {
            "OPENCODE_API_KEY": "",
            "AGENTROUTER_API_KEY": "",
            "GEMINI_API_KEY": "",
            "GOOGLE_AI_STUDIO_API_KEY": "",
        }
        blank.update(env)
        with mock.patch.dict(os.environ, blank):
            return self.engine_app._get_providers()

    def test_no_keys_configured_means_no_providers(self):
        # The endpoint then answers 503 "AI service is not configured" instead
        # of spending a credential baked into the image.
        self.assertEqual([], self._providers_with())

    def test_environment_keys_are_still_used(self):
        names = [
            provider["name"]
            for provider in self._providers_with(GEMINI_API_KEY="test-gemini-key")
        ]
        self.assertIn("gemini", names)

    def test_blank_keys_are_not_treated_as_configured(self):
        # An empty dashboard value is the common way a "rotated" key arrives.
        self.assertEqual(
            [], self._providers_with(GEMINI_API_KEY="   ", OPENCODE_API_KEY=" ")
        )


if __name__ == "__main__":
    unittest.main()
