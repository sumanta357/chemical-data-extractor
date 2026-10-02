# Monetization Implementation Plan — Phase 1 (Accounts, Quotas, Razorpay)

Status: implementation plan written **before** any code change, per the requested workflow.

---

## 1. Existing architecture (verified by reading the code)

```
Browser (Next.js static export, app/ + components/)
  └── fetch('/api/...')            same-origin
        ↓
FastAPI (python-api/app.py, 2082 lines)          ← single runtime process
  ├── SQLite search state          SEARCH_DB=/tmp/scigraph_searches.db
  ├── POST /api/search             creates job, spawns background task
  ├── GET  /api/search/{id}        status + export files   (polled every 1s by the UI)
  ├── GET  /api/search/{id}/log    incremental log
  ├── GET  /api/search/{id}/graph  nodes/edges as Cytoscape JSON
  ├── GET  /api/exports/{file}    download export
  ├── GET  /api/searches          recent searches
  ├── POST /api/analyze           AI analyst (provider pool + circuit breaker)
  ├── GET  /api/health
  └── GET  /{full_path:path}      SPA catch-all  ← **line 912, must stay LAST**
        ↓
  subprocess: api/scigraph.py (engine) → api/enrich_exports.py (enrichment)
```

Deployment: single container (`Dockerfile`), Render, static UI built in stage 1 and
served by FastAPI in stage 2. Frontend has **no** API routes (`rm -rf app/api` at build).

### Hard constraints for this work

| # | Constraint | Consequence |
|---|---|---|
| C1 | SPA catch-all at `app.py:912` matches any unmatched path | Every new route **must** be registered before it, or the UI gets `index.html` instead of JSON |
| C2 | `POST /api/search` is the product's core endpoint | Quota logic must be **opt-in** (env-gated) and must never change today's behaviour when disabled |
| C3 | No DB migrations allowed on the existing `searches` table | New data goes to a **separate** SQLite file; existing DB untouched |
| C4 | Secrets must never reach the browser | Razorpay `key_secret` lives only in env; frontend gets only `key_id`, fetched from an endpoint |
| C5 | Frontend deps are minimal (next/react/tailwind), dark theme, no icon library | New UI reuses `.glass-card`, cyan accent, Inter/JetBrains Mono; no new npm packages |
| C6 | Don't change deployment config | Same Docker image, same `CMD`, no new services required to run |

---

## 2. What gets built (Phase 1)

### 2.1 Backend — new additive modules (existing files untouched except `app.py` wiring)

| File | Responsibility |
|---|---|
| `python-api/billing_store.py` | SQLite (WAL) schema + accessors: `users`, `sessions`, `subscriptions`, `orders`, `payments`, `usage_events`, `search_owners`, `webhook_events`. Parameterised queries only. Separate DB file (`BILLING_DB`). |
| `python-api/auth.py` | PBKDF2-HMAC-SHA256 password hashing (stdlib), opaque session tokens (hashed at rest), HttpOnly/SameSite cookies, FastAPI dependencies `current_user_optional` / `current_user_required`. |
| `python-api/billing.py` | Plan catalog (INR), Razorpay REST client, order creation, payment fetch, **checkout + webhook signature verification**, entitlement/quota resolution, usage recording. |
| `python-api/routes_billing.py` | `APIRouter` with all `/api/account/*` and `/api/billing/*` endpoints. |

### 2.2 Wiring into `app.py` (3 small, reversible edits)

1. `app.include_router(billing_router)` — inserted **before** the SPA catch-all (C1).
2. `start_search`: resolve the caller's account and call `require_search_entitlement(...)`.
   - `REQUIRE_AUTH` **not set** → function returns immediately, endpoint behaves exactly as today (C2).
   - Records `search_id → user_id` ownership so usage can be attributed on completion.
3. `run_search_in_background` completion path: `record_search_usage(...)` (best-effort, wrapped in try/except).

### 2.3 Frontend — new files + minimal edits

| File | Purpose |
|---|---|
| `lib/billing.ts` | Typed client: register/login/logout/me, plans, create order, verify payment, usage. |
| `components/AccountPanel.tsx` | Sign in / create account, plan + usage display, Razorpay Checkout.js trigger, payment result states. Matches existing dark/cyan design, no icons. |
| `app/page.tsx` | Header gets the account entry point; `handleSearch` surfaces `402 quota_reached` / `401` from the API. Otherwise unchanged. |

Razorpay Checkout.js is loaded from Razorpay's CDN at click time (no npm dep).
Only `RAZORPAY_KEY_ID` (publishable) is exposed, and only via `GET /api/billing/config`.

### 2.4 Payment flow (implemented end to end)

```
1. User picks a plan  →  POST /api/billing/create-order {plan}
2. Server validates the plan server-side, builds the Razorpay order
   (amount in paise, INR, receipt, notes) and returns {order_id, amount, key_id, plan}
3. Browser opens Razorpay Checkout with key_id + order_id
4a. SUCCESS → POST /api/billing/verify-payment {razorpay_order_id, razorpay_payment_id, razorpay_signature}
              → server recomputes HMAC-SHA256(order_id|payment_id, key_secret)
              → constant-time compare → activate subscription → return entitlement
4b. CANCEL/FAIL → nothing is granted client-side; the webhook is the source of truth
5. POST /api/billing/webhook  → verifies X-Razorpay-Signature (HMAC of raw body)
   → payment.captured activates, payment.failed records, refund.processed downgrades
   → event ids deduped (idempotent), unknown events ignored safely
```

Security rules: `key_secret` only from env, never logged, never returned; all signature
comparisons via `hmac.compare_digest`; webhook requires a valid signature; subscription
activation only ever happens server-side after verification; payments are idempotent on
`payment_id`; plan prices are resolved server-side from the catalog (client cannot send an amount).

### 2.5 Plans

| Plan | Price | Searches / month | Notes |
|---|---|---|---|
| Free | ₹0 | 3 | full results, export limit, AI analyst capped |
| Academic | ₹249 | 30 | academic email |
| Starter | ₹499 | 50 | all export formats |
| Pro | ₹1,999 | 300 | batch + API |
| Team | ₹7,499 | 3,000 | 10 seats |

### 2.6 Configuration (env)

`RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` (server only), `RAZORPAY_WEBHOOK_SECRET`
(optional, defaults to key secret), `BILLING_DB`, `REQUIRE_AUTH=0|1`,
`SESSION_TTL_DAYS`, optional `RAZORPAY_PLAN_<PLAN>` ids for Razorpay Subscriptions.

---

## 3. Test plan (`python-api/tests/test_billing.py`, stdlib `unittest`, no network)

- checkout signature accept/reject (incl. tampered payload, wrong secret)
- webhook signature accept/reject, replayed event ignored
- order payload correctness (paise conversion, receipt, notes)
- entitlement/quota per plan, period reset, exhausted quota
- full lifecycle with a stubbed Razorpay transport: order → verify → active → usage
  increments → quota exhausted → refund deactivates
- auth: register, login, bad password, session expiry, logout
- **regression**: existing routes still registered, SPA catch-all still last,
  `POST /api/search` behaves exactly as before with billing disabled

---

## 4. Phase 2 (specified, not built in this pass)

Saved projects + incremental re-runs → weekly change alerts → provenance PDF report →
SMILES/RDKit structure search → prioritisation scoring → GraphRAG bundle → team workspace.
Each builds on the Phase 1 tables (`users`, `subscriptions`, `usage_events`) without
changing Phase 1 contracts.