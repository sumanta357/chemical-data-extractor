# Accounts, Plans & Payments (Razorpay)

Optional monetisation layer for the hosted platform. When it is not configured the
product behaves **exactly** as before: anonymous searches, no accounts, no limits.

---

## What was added

| Area | Files |
|---|---|
| Persistence | `python-api/billing_store.py` — its own SQLite file (`BILLING_DB`), the search database is untouched |
| Auth | `python-api/auth.py` — PBKDF2-HMAC-SHA256 passwords, opaque sessions, HttpOnly cookies |
| Payments | `python-api/billing.py` — plan catalog, Razorpay client, signature verification, quotas |
| HTTP routes | `python-api/routes_billing.py` — `/api/account/*`, `/api/billing/*` |
| UI | `components/AccountPanel.tsx`, `lib/billing.ts`, header entry point in `app/page.tsx` |
| Tests | `python-api/tests/test_billing.py` — 49 tests, no network |
| Email | `python-api/emailer.py` — Resend delivery for verification and reset |
| Directory | `GET /api/admin/academics`, `/admin/academics` page |

`python-api/app.py` changed in five small places only: the billing import, three
helper wrappers, the entitlement gate inside `POST /api/search`, usage recording on
completion, analyst metering, and `app.include_router(...)` **before** the SPA
catch-all route. Nothing else in the search engine, exports, AI analyst or history
was modified.

---

## Plans

| Plan | Price | Searches / month | Analyst messages |
|---|---|---|---|
| Free | ₹0 | 3 | 5 |
| Academic | ₹249 | 30 | 100 |
| Starter | ₹499 | 50 | 250 |
| Professional | ₹1,999 | 300 | 1,500 |
| Team | ₹7,499 | 3,000 | 10,000 |

Prices live in `PLANS` in `python-api/billing.py` and are the **only** source of
truth — the browser can never send an amount.

---

## Environment variables (Render dashboard → Environment)

| Variable | Required | Purpose |
|---|---|---|
| `RAZORPAY_KEY_ID` | for payments | Publishable key id (safe to expose; served to the browser) |
| `RAZORPAY_KEY_SECRET` | for payments | **Server only.** Never logged, never returned to a client |
| `RAZORPAY_WEBHOOK_SECRET` | recommended | Secret configured in the Razorpay dashboard for webhook signature checks |
| `BILLING_DB` | no | Path to the accounts database (default `/tmp/scigraph_billing.db`) |
| `REQUIRE_AUTH` | no | `1` = anonymous visitors must sign in before searching. Default `0` keeps the site open |
| `SESSION_TTL_DAYS` | no | Session lifetime (default 30) |
| `COOKIE_SECURE` | no | `0` only for local http testing |

Use **Razorpay test keys** first (`rzp_test_…`), switch to live keys when you go live.

### Where accounts are stored

The accounts store (`python-api/billing_store.py`) runs on **either** backend,
selected automatically:

| `DATABASE_URL` | Backend | Survives redeploys |
|---|---|---|
| not set | SQLite file at `BILLING_DB` (default `/tmp/scigraph_billing.db`) | ❌ not on `/tmp` |
| set | **PostgreSQL** (Neon or any provider) | ✅ always |

The service logs which one is in use at startup, and prints a warning when accounts
sit on ephemeral storage.

**Postgres / Neon setup**

1. Create a project at [console.neon.tech](https://console.neon.tech)
2. **Connection Details** → copy the **pooled** connection string
3. Render → Environment:

```
DATABASE_URL = postgresql://user:password@ep-xxx.region.neon.tech/neondb?sslmode=require
```

Tables are created automatically on first start — there is no migration step.

Notes:
- Only the accounts store moves to Postgres. The search database (`SEARCH_DB`) stays
  on SQLite, since it holds disposable job state.
- `psycopg[binary]` is in `requirements.txt` but is never imported when no
  `DATABASE_URL` is set, so SQLite deployments are unaffected.
- Run the whole test suite against Postgres with:
  `BILLING_DRIVER=postgres DATABASE_URL='postgresql://...' python3 -m pytest tests/ -q`

**Persistent-disk alternative (no code, no external service)**

1. Render → service → **Disks** → add a disk mounted at `/var/data` (1 GB is enough)
2. Set `BILLING_DB=/var/data/scigraph_billing.db`

### Without either: accounts live on /tmp and are wiped on every deploy

That was the cause of "payment succeeded but the plan never activated": the captured
payment existed at Razorpay while the order record it pointed at had been deleted.

### Razorpay dashboard setup

1. Dashboard → **Settings → Webhooks** → add endpoint
   `https://chemical-data-extractor.onrender.com/api/billing/webhook`
2. Subscribe to: `payment.captured`, `payment.failed`, `refund.processed`
3. Set a webhook secret and paste it into `RAZORPAY_WEBHOOK_SECRET`.

---

## Payment flow

1. The browser asks the server for a plan price (`GET /api/billing/plans`).
2. `POST /api/billing/create-order {plan}` — the server builds the Razorpay order
   (amount from the catalog, notes carry plan + user id) and stores it.
3. Razorpay Checkout opens with `key_id` + `order_id` (key secret never leaves the server).
4. On success the browser posts the response to `POST /api/billing/verify-payment`.
   The server recomputes `HMAC-SHA256(order_id|payment_id, key_secret)`, compares it
   with `hmac.compare_digest`, then confirms the payment with Razorpay's own API
   before activating the plan.
5. `POST /api/billing/webhook` is the source of truth: signature verified against the
   raw body, event ids deduplicated, so retries and replays cannot double-credit.

Failure, cancellation and pending states are all surfaced in the UI
("Payment cancelled. Nothing was charged.", verification errors, payment-status lookup).

---

## Testing

```bash
cd python-api && python3 -m pytest tests/test_billing.py -q
```

Covers: password hashing, session lifecycle, plan catalog integrity, checkout and
webhook signature accept/reject, order payload correctness, capture → activation →
usage → quota exhaustion, duplicate webhook replays, refunds, account flows over HTTP,
and a regression suite proving the existing search API is unchanged (including that
anonymous search still works and that the billing routes are mounted before the SPA
catch-all).

For a real end-to-end run with Razorpay's sandbox, set test keys locally and use
Razorpay's test card `4111 1111 1111 1111` (any future expiry/CVV).

---

# Accounts security, academic directory

Three additions on top of the layer above. All of them are **off by default**
except the profile fields, so an unconfigured deployment behaves exactly as
before.

## 1. Email verification & forgotten passwords (Resend)

Existing accounts, sessions and PBKDF2 hashing are unchanged — this only adds
one-time tokens and the email that carries them.

| Route | Purpose |
|---|---|
| `POST /api/account/forgot-password` | always answers `{"status":"sent"}`, whether or not the address exists (no user enumeration) |
| `POST /api/account/reset-password` | `{token, password}` — sets the password and **deletes every session** |
| `GET /api/account/verify-email?token=` | confirms the address, marks `email_verified_at` |
| `POST /api/account/resend-verification` | issues a fresh link (generic response) |

* Tokens live in `auth_tokens`, are SHA-256 hashed, **single use**, and a reissued
  link invalidates the previous one.
* Throttled to `RESET_REQUESTS_PER_HOUR` per account.
* Delivery is one HTTPS call to Resend (`python-api/emailer.py`). It fails soft:
  a missing key or a provider outage logs the link and never fails signup, so the
  account is still created and the token still works.
* `REQUIRE_VERIFIED_EMAIL=1` blocks sign-in until the address is confirmed
  (off by default — existing accounts are unaffected).

### New environment variables

| Variable | Default | Meaning |
|---|---|---|
| `RESEND_API_KEY` | — | Resend API token. Unset ⇒ emails are not delivered, links are logged |
| `EMAIL_FROM` | `SciGraph <onboarding@resend.dev>` | verified sender |
| `APP_URL` | `http://localhost:8000` | public origin used to build links |
| `EMAIL_EXPOSE_LINKS` | `0` | **local development only** — returns the link in the API response |
| `REQUIRE_VERIFIED_EMAIL` | `0` | require a confirmed address before sign-in |
| `VERIFY_TOKEN_TTL_MINUTES` | `1440` | confirmation link lifetime |
| `RESET_TOKEN_TTL_MINUTES` | `60` | reset link lifetime |
| `RESET_REQUESTS_PER_HOUR` | `5` | per-account reset throttle |

Two new static routes back the emails: `/reset-password` and `/verify-email`
(`app/reset-password`, `app/verify-email`).

## 2. Academic profile & customer directory

Signed-in users fill in their academic details from the account panel
(**Academic profile**); registration accepts the same block as `profile`.

Fields on `users`: `academic_email`, `institute_name`, `institute_id`, `phone`,
`academician_type`, plus `email_verified_at`. Added as idempotent `ALTER`s by
`db.add_missing_columns()`, so a database created before this release is
migrated in place.

`GET /api/admin/academics` — the directory. Filters: `q` (free text across every
field), `academic_email`, `institute_name`, `institute_id`, `phone`,
`academician_type`, `verified`, `plan`. Sorting: any of the filter columns plus
`email`, `name`, `created_at`, `searches_used`, `order=asc|desc`, paginated with
`limit`/`offset` and a `total`.

* The sort column is whitelisted in the store; it can never reach SQL as free text.
* Every filter is a bound parameter, and each sortable column is indexed.
* UI: `/admin/academics` — token unlock, live filters, click-to-sort headers,
  pagination. The token is kept in `localStorage` and sent as `X-Admin-Token`.

| Variable | Default | Meaning |
|---|---|---|
| `ADMIN_TOKEN` | — | shared secret for the directory. **Unset ⇒ the endpoint answers 404** and no customer data is exposed |

## 3. Search counting

No change was made — the metering path is plan-agnostic by design
(`attach_search_owner` → `claim_search_owner` → `increment_counters`), and two
searches accumulate to two on Academic, Professional and Team alike, across
sessions. `python-api/tests/test_accounts.py` now pins that behaviour so a
regression cannot creep in.

## Tests

```bash
cd python-api && python3 -m pytest tests/ -q
```

128 tests: the original billing suite, the new accounts suite (directory
filters/sorting, admin token, verification, reset, throttle, token single-use,
per-plan quota accumulation) and static-route resolution for the new pages.