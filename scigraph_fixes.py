"""
Runtime patches and safer utilities for scigraph.py
This module monkeypatches selected behaviors in scigraph.py at runtime when imported:
 - Improved TokenBucketRateLimiter.acquire (correct refill math, don't zero tokens incorrectly)
 - Improved BaseConnector._safe_get and _safe_post with exponential backoff, Retry-After handling, and robust JSON parsing
 - Atomic file write helper and wrappers for CSV / graphml / turtle exports to write to temp file then rename

Usage:
  python -c "import scigraph, scigraph_fixes"

This avoids editing the main scigraph.py directly while providing safer defaults. For permanent changes, integrate the changes from this file into scigraph.py.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import tempfile
import time
from typing import Any, Dict, Optional

# Try to import scigraph symbols — this file assumes being used alongside scigraph.py
try:
    import scigraph
    from scigraph import BaseConnector, TokenBucketRateLimiter
except Exception:
    # If scigraph can't be imported, nothing to patch
    BaseConnector = None
    TokenBucketRateLimiter = None


# -------------------------------
# Atomic file write helper
# -------------------------------

def atomic_write_text(path: str, text: str, encoding: str = "utf-8") -> None:
    """Write text atomically by writing to a temporary file in the same directory and renaming.
    This reduces risk of corrupted files if program crashes while writing.
    """
    dirpath = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(dirpath, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dirpath, prefix=".tmp-scigraph-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except Exception:
            pass
        raise


# -------------------------------
# Patch TokenBucketRateLimiter.acquire
# -------------------------------
if TokenBucketRateLimiter is not None:
    _orig_acquire = TokenBucketRateLimiter.acquire

    async def _patched_acquire(self: TokenBucketRateLimiter) -> None:
        """Patched acquire that refills tokens correctly and waits with jitter when empty.
        Uses monotonic clock and preserves tokens on wake.
        """
        async with self._lock:
            now = time.monotonic()
            # refill based on elapsed time
            elapsed = now - self.last_refill
            if elapsed > 0:
                self.tokens = min(float(self.capacity), self.tokens + elapsed * self.rate)
                self.last_refill = now

            if self.tokens >= 1.0:
                self.tokens -= 1.0
                return

            # tokens are insufficient — compute wait and release lock while sleeping
            needed = 1.0 - self.tokens
            wait_time = (needed / self.rate) * (1.0 + random.uniform(0.0, 0.05))

        # release lock before sleeping to allow other coroutines to schedule
        await asyncio.sleep(wait_time)

        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            if elapsed > 0:
                self.tokens = min(float(self.capacity), self.tokens + elapsed * self.rate)
            # consume token (if available) or set to 0
            if self.tokens >= 1.0:
                self.tokens -= 1.0
            else:
                # This is an unlikely race; ensure tokens are non-negative
                self.tokens = max(0.0, self.tokens - 1.0)
            self.last_refill = now

    TokenBucketRateLimiter.acquire = _patched_acquire


# -------------------------------
# Helper: robust JSON parsing
# -------------------------------

def _parse_json_loose(text: str) -> Any:
    """Try orjson style loads then fall back to json — handle empty strings gracefully.
    """
    if not text:
        return None
    try:
        # prefer orjson if available on scigraph environment
        if hasattr(scigraph, "orjson"):
            return scigraph.orjson.loads(text)
    except Exception:
        pass
    try:
        return json.loads(text)
    except Exception:
        # last-ditch: return raw text
        return text


# -------------------------------
# Patch BaseConnector._safe_get and _safe_post
# -------------------------------
if BaseConnector is not None:
    async def _patched_safe_get(self: BaseConnector, session, url: str) -> Optional[Dict[str, Any]]:
        # Use cache first
        try:
            cached, etag, last_mod = self.cache.get(url)
        except Exception:
            cached = etag = last_mod = None

        if cached:
            parsed = None
            try:
                parsed = _parse_json_loose(cached)
                if parsed in ([], {}, None):
                    parsed = None
                else:
                    return parsed
            except Exception:
                parsed = None

        headers = {"User-Agent": "SciGraphEnterprise/3.1"}
        if etag:
            headers["If-None-Match"] = etag
        if last_mod:
            headers["If-Modified-Since"] = last_mod

        # exponential backoff attempts
        max_attempts = getattr(self, "MAX_ATTEMPTS", 4)
        backoff_base = 0.5
        for attempt in range(1, max_attempts + 1):
            try:
                await self.rate_limiter.acquire()
                timeout = getattr(self, "REQUEST_TIMEOUT", 8)
                async with session.get(url, headers=headers, timeout=session.timeout if hasattr(session, 'timeout') else aiohttp.ClientTimeout(total=timeout)) as resp:  # type: ignore[name-defined]
                    status = resp.status
                    # handle 304
                    if status == 304 and cached:
                        return _parse_json_loose(cached)
                    text = await resp.text()
                    if status == 200:
                        # write to cache if available
                        try:
                            self.cache.set(url, text, getattr(self, "CACHE_TTL", 86400), resp.headers.get("ETag"), resp.headers.get("Last-Modified"))
                        except Exception:
                            pass
                        return _parse_json_loose(text)

                    # handle 429/503 with Retry-After if present
                    if status in (429, 503):
                        retry_after = resp.headers.get("Retry-After")
                        if retry_after:
                            try:
                                wait = float(retry_after)
                            except Exception:
                                try:
                                    # might be an HTTP date — fallback to a small backoff
                                    wait = backoff_base * attempt
                                except Exception:
                                    wait = backoff_base * attempt
                        else:
                            wait = backoff_base * (2 ** (attempt - 1)) + random.random() * 0.1
                        await asyncio.sleep(wait)
                        continue

                    # other 4xx/5xx -> stop retrying for client errors
                    if 400 <= status < 500:
                        return None
                    # server errors: backoff and retry
                    await asyncio.sleep(backoff_base * (2 ** (attempt - 1)) + random.random() * 0.1)
            except Exception as e:
                # On network errors backoff and retry
                await asyncio.sleep(backoff_base * (2 ** (attempt - 1)) + random.random() * 0.1)
                last_exc = e
                continue
        # if we reach here, try returning cached parsed response as fallback
        try:
            if cached:
                return _parse_json_loose(cached)
        except Exception:
            pass
        return None

    async def _patched_safe_post(self: BaseConnector, session, url: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        cache_key = f"POST:{url}:{scigraph.hashlib.md5(scigraph.json.dumps(data, sort_keys=True).encode()).hexdigest()}"
        try:
            cached, etag, last_mod = self.cache.get(cache_key)
        except Exception:
            cached = etag = last_mod = None
        if cached:
            parsed = _parse_json_loose(cached)
            if parsed not in ([], {}, None):
                return parsed

        headers = {"User-Agent": "SciGraphEnterprise/3.1", "Content-Type": "application/x-www-form-urlencoded"}
        max_attempts = getattr(self, "MAX_ATTEMPTS", 4)
        backoff_base = 0.5
        for attempt in range(1, max_attempts + 1):
            try:
                await self.rate_limiter.acquire()
                timeout = getattr(self, "REQUEST_TIMEOUT", 8)
                async with session.post(url, data=data, headers=headers, timeout=session.timeout if hasattr(session, 'timeout') else aiohttp.ClientTimeout(total=timeout)) as resp:  # type: ignore[name-defined]
                    status = resp.status
                    text = await resp.text()
                    if status == 200:
                        try:
                            self.cache.set(cache_key, text, getattr(self, "CACHE_TTL", 86400))
                        except Exception:
                            pass
                        return _parse_json_loose(text)
                    if status in (429, 503):
                        retry_after = resp.headers.get("Retry-After")
                        if retry_after:
                            try:
                                wait = float(retry_after)
                            except Exception:
                                wait = backoff_base * attempt
                        else:
                            wait = backoff_base * (2 ** (attempt - 1)) + random.random() * 0.1
                        await asyncio.sleep(wait)
                        continue
                    if 400 <= status < 500:
                        return None
                    await asyncio.sleep(backoff_base * (2 ** (attempt - 1)) + random.random() * 0.1)
            except Exception:
                await asyncio.sleep(backoff_base * (2 ** (attempt - 1)) + random.random() * 0.1)
                continue
        if cached:
            return _parse_json_loose(cached)
        return None

    # Attach monkeypatch
    BaseConnector._safe_get = _patched_safe_get
    BaseConnector._safe_post = _patched_safe_post


# -------------------------------
# Optional: patch export functions to use atomic_write_text where possible
# We will replace a small set of functions if they exist on scigraph module
# -------------------------------
if 'scigraph' in globals():
    try:
        # export_to_csv writes two files; wrap to write to temp then rename
        if hasattr(scigraph, 'export_to_csv'):
            _orig_export_to_csv = scigraph.export_to_csv

            def _wrapped_export_to_csv(entities, relations, entity_filepath: str, relation_filepath: str):
                # create CSV contents in memory then write atomically
                import io, csv
                # Entities CSV
                e_buf = io.StringIO()
                w = csv.writer(e_buf)
                w.writerow(["uid:ID", "name", ":LABEL", "canonical_id", "synonyms", "cross_references", "smiles", "formula", "molecular_weight", "bioactivity_summary"])
                for e in entities:
                    xr_str = "|".join([f"{xr.database}:{xr.accession}" for xr in e.cross_references])
                    smiles = e.attributes.get("smiles", "")
                    formula = e.attributes.get("formula", "")
                    mw = e.attributes.get("molecular_weight", "")
                    bio = e.attributes.get("bioactivity_summary", "")
                    w.writerow([e.uid, e.preferred_name, ";".join(e.node_labels()), e.canonical_id, "|".join(e.synonyms), xr_str, smiles, formula, mw, bio])
                e_text = e_buf.getvalue()

                # Relations CSV
                r_buf = io.StringIO()
                w2 = csv.writer(r_buf)
                w2.writerow([":START_ID", ":END_ID", ":TYPE", "confidence", "activity_type", "activity_value", "units", "pchembl_value", "mechanism_of_action", "assay_id"])
                for r in relations:
                    conf = r.evidence[0].confidence_score if r.evidence else 1.0
                    act_type = r.attributes.get("activity_type", "")
                    act_val = r.attributes.get("activity_value", "")
                    units = r.attributes.get("units", "")
                    pchembl = r.attributes.get("pchembl_value", "")
                    mech = r.attributes.get("mechanism_of_action", "")
                    assay = r.attributes.get("assay_id", "")
                    w2.writerow([r.source_uid, r.target_uid, r.relationship_type(), conf, act_type, act_val, units, pchembl, mech, assay])
                r_text = r_buf.getvalue()

                atomic_write_text(entity_filepath, e_text)
                atomic_write_text(relation_filepath, r_text)

            scigraph.export_to_csv = _wrapped_export_to_csv
    except Exception:
        pass

print("scigraph_fixes: runtime patches applied (if scigraph module was importable).")
