# Accounts, Plans & Payments (Razorpay)

Optional monetisation layer for the hosted platform. When it is not configured the
product behaves **exactly** as before: anonymous searches, no accounts, no limits.

Accounts are otherwise **enforced by default** (`REQUIRE_AUTH=1`): a search must
carry a Supabase access token, so every search is attributable to an account and
can be metered against that account's plan. Set `REQUIRE_AUTH=0` to reopen the
unmetered guest tier.

---

## What was added

| Area | Files |
|---|---|
| Persistence | `python-api/billing_store.py` — its own SQLite file (`BILLING_DB`) or Postgres, the search database is untouched |
| Auth | `python-api/supabase_auth.py` + `python-api/auth.py` — Supabase owns sign-in; this app maps the verified token onto its billing account |
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
| `REQUIRE_AUTH` | no | Sign-in gate. **Default `1`**: anonymous visitors must sign in before searching or using the AI analyst. Set `0` to reopen the unmetered guest tier (demos, landing-page previews) |
| `SUPABASE_URL` | for accounts | Supabase project URL (identity — see below) |
| `SUPABASE_ANON_KEY` | for accounts | Supabase **anon / publishable** key, never `service_role` |
| `ADMIN_TOKEN` | no | Shared secret for the academic directory. Unset ⇒ the endpoint 404s |
| `ACADEMIC_VERIFICATION_SECRET` | for the Academic plan | Signing key for academic attestations. Falls back to `RAZORPAY_KEY_SECRET`; if neither is set, Academic checkout is refused rather than allowed |
| `ACADEMIC_ALLOWED_DOMAINS` | no | Comma-separated institutional domains. When set, **only** these (and their subdomains) are eligible, replacing the built-in suffix list |
| `ACADEMIC_TOKEN_TTL_SECONDS` | no | Attestation lifetime, default `1800` |
| `ACADEMIC_MAX_ATTEMPTS_PER_HOUR` | no | Verification attempts per account per hour, default `5` |
| `ACADEMIC_REQUIRE_MAILBOX_PROOF` | no | Set once SMTP is configured; tells the UI to promise a confirmation email |
| `SEARCHES_PER_IP_HOUR` | no | Searches one IP address may start per hour, default `30`. Bounds account-farming: sign-up happens against Supabase from the browser, so the server cannot rate-limit account creation — this limits the work an address can obtain |
| `PAID_SEARCHES_PER_IP_PER_HOUR` | no | The same ceiling for accounts on a paid plan, default `200`, so a shared campus/office NAT cannot lock out customers who have paid |
| `SEARCH_ENRICH_IMAGES` | no | Download a 2D structure image per compound during enrichment. **Default `1`**. Set `0` on a memory-constrained host (the Render free tier's 512MB) to drop the thumbnails, which is the peak-memory step of a search; `enriched_data.xlsx` and every CSV are still produced |

### The search stall watchdog

A search runs as a subprocess (`api/scigraph.py`) and is abandoned if the engine
stops making progress, which surfaces in the UI as `Failed: engine stalled`. The
thresholds are module constants in `python-api/app.py`, not env vars, because a
watchdog that can be switched off is not one:

| Constant | Value | Meaning |
|---|---|---|
| `STALL_POLL_SECONDS` | `10` | How often the engine is sampled for both signs of life (CPU time from `/proc`, and printed output) |
| `STALL_TIMEOUT` | `900` (15 min) | Seconds a search must be silent **and** idle before it is killed |
| `MAX_SEARCH_SECONDS` | `3000` (50 min) | Hard ceiling for one search, however busy it is |

Silence alone is never enough. The engine prints between steps, and a long
connector batch on the throttled free tier is slow rather than silent — a 1-hop
protein query can sit inside one step for minutes and still be minutes from
finishing. The watchdog therefore tracks two independent signs of life and
resets its quiet timer on either: the subprocess's CPU time advancing
(`/proc`), or the engine printing a line. A stall means silent **and** using no
CPU. Either signal alone would kill a live search — a busy-but-quiet engine
looks dead to an output-only watchdog, and an engine parked on a slow connector
request looks dead to a CPU-only one. If CPU time cannot be read at all, that
counts as unknown rather than as idle: unknown is not idle.

The 50-minute ceiling is unconditional, because a process can spin without ever
printing and a search must not hold the single search slot for ever.

Both limits are deliberately generous. On the 512MB free tier a large query is
slow rather than hung, so a premature kill throws away real work; a genuinely
dead process is rare, and the ceiling is what guarantees the slot is released.

### Why there is a per-IP limit at all

Account creation is a `supabase.auth.signUp()` call made **in the browser**, so it
never reaches this server and cannot be rate-limited here. Combined with Supabase's
`mailer_autoconfirm` setting (which returns a session immediately, with no email to
prove), anyone can mint unlimited throwaway accounts and spend a fresh free-plan
allowance on each.

The enforceable chokepoint is the search. `POST /api/search` charges every start
against a per-address hourly budget, returning `429` with `code: "rate_limited"`
once it is spent. Paid plans get a much larger ceiling, and the per-account plan
quota still applies independently — whichever ceiling is reached first wins.

The durable fix is email confirmation: configure SMTP in Supabase and turn
**Confirm email** on, so a sign-up yields no session until the address is proven.
That is a dashboard change, not a code change. Until then this limit raises the
cost of farming rather than eliminating it — a determined attacker with many IP
addresses can still exceed it.

---

## Academic plan verification

The Academic plan is **not** an ordinary paid plan. Buying it requires a
server-side eligibility check, and the check is repeated when the payment is
activated — so skipping the UI, replaying a captured payment or hitting the
webhook directly still cannot grant an Academic subscription.

```
POST /api/account/academic/verify     →  status + short-lived attestation token
POST /api/billing/create-order        →  403 academic_verification_required unless
                                         verified AND a live token is presented
POST /api/billing/verify-payment      →  signature + provider confirmation
POST /api/billing/webhook             →  re-checks the durable verified status
```

Two distinct facts are tracked, deliberately:

| | Meaning | Lifetime |
|---|---|---|
| `academic_status = 'verified'` | the address passed eligibility | durable until the profile address changes |
| attestation token | verification happened moments ago | `ACADEMIC_TOKEN_TTL_SECONDS` (30 min default) |

Changing `academic_email` on the profile **resets** the verification, so a
verified account cannot swap in a different address and keep the entitlement.
Every attempt — accepted or refused — is written to `academic_verifications`
for the audit trail, and one institutional address may back only one Academic
subscription.

### What this does *not* prove yet

This verifies that the **domain** is an academic institution. It cannot prove
the requester controls the mailbox, because no confirmation mail is sent until
SMTP is configured. When that is set, turn on `ACADEMIC_REQUIRE_MAILBOX_PROOF`
so the UI stops implying more assurance than we actually have, and add the
mailbox confirmation step.

### Country TLDs

`.ca`, `.de`, `.uk`, `.in` and friends carry commercial providers as well as
schools, so they are **not** auto-accepted (`yahoo.ca` would otherwise get the
Academic plan). Serve those countries by listing institutions explicitly:

```
ACADEMIC_ALLOWED_DOMAINS=uwaterloo.ca,ubc.ca,uni-bonn.de,ox.ac.uk
```

The consumer-mailbox blocklist is applied *before* the allowlist, so adding a
broad entry still cannot wave through `gmail.com` or `yahoo.ca`.

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

Covers: the plan catalog, checkout and webhook signature accept/reject, order
payload correctness, capture → activation → usage → quota exhaustion, duplicate
webhook replays, refunds, Supabase session resolution over HTTP, the academic
directory filters and sorting, and a regression suite proving the existing search
API is unchanged (including that a signed-in search still starts normally, that
the sign-in gate answers `401` with `code: "auth_required"`, and that the
billing routes are mounted before the SPA catch-all).

For a real end-to-end run with Razorpay's sandbox, set test keys locally and use
Razorpay's test card `4111 1111 1111 1111` (any future expiry/CVV).


---

# Authentication (Supabase) and the academic directory

## Authentication — Supabase owns identity

Sign-up, sign-in, "Sign in with Google", password reset and email confirmation
are **Supabase Auth**. This app never sees a password, never issues a session,
and has no session table. The flow is:

1. The browser signs in with `@supabase/supabase-js` (created at runtime from
   `/api/billing/config`, so rotating the key is an env change, not a rebuild).
2. Supabase returns an access token, which the browser sends as
   `Authorization: Bearer <token>`.
3. The server exchanges that token for the user at Supabase's
   `GET /auth/v1/user` and maps the returned **Supabase user id** onto a row in
   our own `users` table — created on first sight. Plans, quota, orders and the
   academic profile all live there, so nothing about billing moved.

A bad, expired or absent token is treated as *anonymous*, never as an error:
browsing the tool without an account behaves exactly as it always has.

```bash
GET /api/account/me          # always 200: the signed-in account + entitlement, or anonymous
GET|PUT /api/account/profile # the academic profile
GET /api/account/usage       # quota, payments, recent searches
```

### Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `SUPABASE_URL` | — | project URL, e.g. `https://xxxx.supabase.co` |
| `SUPABASE_ANON_KEY` | — | the **anon / publishable** key (public by design). Never the `service_role` key |

Unset ⇒ `/api/account/me` reports anonymous and the panel shows "sign-in is not
configured". Supabase dashboard: **Authentication → Sign In / Providers** —
enable **Google** (and Email if you want passwords), and set
**Authentication → URL Configuration → Site URL** plus a redirect to
`https://chemical-data-extractor.onrender.com/`.

### Accounts started fresh

Previous accounts, sessions and one-time tokens are gone: `sessions` and
`auth_tokens` are no longer in the schema, and accounts are matched on
`supabase_user_id`. To clear the old rows, run this once against Neon:

```sql
DELETE FROM users;   -- everything predating Supabase
DROP TABLE IF EXISTS sessions;
DROP TABLE IF EXISTS auth_tokens;
```

## Academic profile & customer directory

Signed-in users fill in their academic details from the account panel
(**Academic profile**). Fields on `users`: `academic_email`, `institute_name`,
`institute_id`, `phone`, `academician_type`, `email_verified_at`. Added as
idempotent `ALTER`s by `db.add_missing_columns()`, so an existing database is
migrated in place.

`GET /api/admin/academics` — filters: `q` (free text across every field),
`academic_email`, `institute_name`, `institute_id`, `phone`,
`academician_type`, `verified`, `plan`. Sorting on any of those plus `email`,
`name`, `created_at`, `searches_used`; paginated with `limit`/`offset` + `total`.

* The sort column is whitelisted in the store — it can never reach SQL as free text.
* Every filter is a bound parameter, and every sortable column is indexed.
* UI: `/admin/academics`. The token is kept in `localStorage` and sent as
  `X-Admin-Token`.

| Variable | Default | Meaning |
|---|---|---|
| `ADMIN_TOKEN` | — | shared secret for the directory. **Unset ⇒ the endpoint answers 404** |

## Search counting

Unchanged and plan-agnostic by design (`attach_search_owner` →
`claim_search_owner` → `increment_counters`). Two searches accumulate to two on
Academic, Professional and Team alike, across sessions and sign-outs.

## Tests

```bash
cd python-api && python3 -m pytest tests/ -q
```

133 tests, no network: Supabase is stubbed at the HTTP boundary, Razorpay at
the request layer.
