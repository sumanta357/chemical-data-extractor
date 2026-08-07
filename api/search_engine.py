"""
Hosted search engine — Freebuff/Vercel Python serverless function.

Exposes POST /api/search_engine which runs the full scigraph pipeline
(scigraph.py) followed by the enrichment pipeline (enrich_exports.py)
and returns:

    {
      "status": "completed",
      "log": [ ...stdout lines... ],
      "files": { "<relative path>": "<base64>" }
    }

The Next.js app falls back to this endpoint in production where the
runtime is Node-only and cannot spawn python3 (see lib/search-engine.ts).
"""
import base64
import contextlib
import io
import json
import os
import traceback

WORKSPACE_DIR = "/tmp/scigraph_data"
EXPORT_DIR = "/tmp/scigraph_export"
# Keep the JSON response comfortably under Vercel's ~4.5 MB response limit.
MAX_RESPONSE_BYTES = 3_500_000


def json_response(status: int, payload: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(payload),
    }


def _run_pipeline(query: str, query_type: str, hops: int) -> dict:
    # Vercel function filesystem is read-only outside /tmp, so move to a
    # writable directory BEFORE importing the engines (they may write
    # caches / data files relative to the current working directory).
    os.makedirs("/tmp", exist_ok=True)
    os.chdir("/tmp")
    os.environ.setdefault("MPLCONFIGDIR", "/tmp/mplconfig")
    os.environ.setdefault("TMPDIR", "/tmp")

    from scigraph import run_automated_search  # noqa: E402
    from enrich_exports import run_enrichment  # noqa: E402

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        run_automated_search(
            query,
            WORKSPACE_DIR,
            EXPORT_DIR,
            max_hops=hops,
            query_type=query_type,
        )
        run_enrichment(EXPORT_DIR)

    log = buf.getvalue().splitlines()

    # Collect export files (base64), smallest first, until we hit the
    # response-size budget. Larger files are skipped with a log note.
    candidates = []
    for root, _dirs, names in os.walk(EXPORT_DIR):
        for name in names:
            full = os.path.join(root, name)
            if not os.path.isfile(full):
                continue
            rel = os.path.relpath(full, EXPORT_DIR).replace(os.sep, "/")
            candidates.append((rel, os.path.getsize(full)))
    candidates.sort(key=lambda item: item[1])

    files: dict = {}
    total = 0
    skipped = []
    for rel, size in candidates:
        if total + size > MAX_RESPONSE_BYTES:
            skipped.append(rel)
            continue
        with open(os.path.join(EXPORT_DIR, rel.replace("/", os.sep)), "rb") as fh:
            files[rel] = base64.b64encode(fh.read()).decode("ascii")
        total += size

    if skipped:
        log.append(
            "⚠️  Production response-size limit reached — omitted: "
            + ", ".join(skipped[:10])
            + ("…" if len(skipped) > 10 else "")
            + " (run locally for full exports)"
        )

    return {"status": "completed", "log": log, "files": files}


def handler(event: dict, context) -> dict:
    """Vercel Python function entry point."""
    try:
        try:
            body = json.loads(event.get("body") or "{}")
        except (TypeError, ValueError):
            body = {}
        query = (body.get("query") or "").strip()
        if not query:
            return json_response(400, {"detail": "Query is required"})
        query_type = body.get("query_type") or "auto"
        try:
            hops = int(body.get("hops") or 1)
        except (TypeError, ValueError):
            hops = 1
        hops = max(1, min(4, hops))

        result = _run_pipeline(query, query_type, hops)
        return json_response(200, result)
    except Exception as exc:  # pragma: no cover - defensive
        return json_response(
            500,
            {
                "status": "failed",
                "detail": str(exc),
                "trace": traceback.format_exc(),
            },
        )


if __name__ == "__main__":  # local smoke test:  python3 api/search.py '{"query":""}'
    import sys

    event = {"body": sys.argv[1] if len(sys.argv) > 1 else "{}"}
    print(handler(event, None))
