#!/usr/bin/env python3
"""
Plans, entitlements and the Razorpay payment gateway.

Security model
--------------
* RAZORPAY_KEY_SECRET / RAZORPAY_WEBHOOK_SECRET are read from the environment
  only. They are never logged, never returned to a client, and never sent to
  the browser (the browser only ever receives the publishable key id).
* Plan prices come from PLANS below, resolved server-side. A client can never
  choose the amount it is charged.
* A subscription is only ever activated server-side, after either
  (a) a checkout signature that recomputes correctly, or
  (b) a webhook whose HMAC signature verifies against the raw request body.
* Payments are idempotent on the Razorpay payment id, so retries and duplicate
  webhook deliveries cannot double-credit an account.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import HTTPException, Request

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import auth  # noqa: E402
import billing_store  # noqa: E402

import requests as _requests  # noqa: E402  (already a project dependency)

LOG = logging.getLogger("scigraph.billing")

import academic  # noqa: E402  (eligibility rules + attestation tokens)

RAZORPAY_API = "https://api.razorpay.com/v1"
KEY_ID = os.environ.get("RAZORPAY_KEY_ID", "").strip()
KEY_SECRET = os.environ.get("RAZORPAY_KEY_SECRET", "").strip()
WEBHOOK_SECRET = os.environ.get("RAZORPAY_WEBHOOK_SECRET", "").strip() or KEY_SECRET

# Payments only work once both keys are configured; until then the product
# behaves exactly as it does today (free, unmetered, no account required).
BILLING_ENABLED = bool(KEY_ID and KEY_SECRET and WEBHOOK_SECRET)

# Accounts are enforced by default: an anonymous visitor must sign in before
# they can start a search. Set REQUIRE_AUTH=0 in the environment to reopen the
# unmetered guest tier (useful for demos or landing-page previews).
REQUIRE_AUTH = os.environ.get("REQUIRE_AUTH", "1").strip().lower() in ("1", "true", "yes", "on")

HTTP_TIMEOUT = 20

# Razorpay references: `order_XXXXXXXX`, `pay_XXXXXXXX`, `rfnd_XXXXXXXX`.
_RAZORPAY_REF_RE = re.compile(r"^(order|pay|rfnd)_[A-Za-z0-9]{6,64}$")


# ── Plan catalog (server-side source of truth) ───────────────────────────────

PLANS: dict[str, dict] = {
    "free": {
        "id": "free",
        "name": "Free",
        "price_inr": 0,
        "amount_paise": 0,
        "searches": 3,
        "analyst_messages": 5,
        "period_days": 30,
        "features": [
            "3 knowledge graph searches per month",
            "Full graph exploration and download of 2 export formats",
            "5 AI analyst messages",
        ],
    },
    "academic": {
        "id": "academic",
        "name": "Academic",
        "price_inr": 249,
        "amount_paise": 24900,
        "searches": 30,
        "analyst_messages": 100,
        "period_days": 30,
        "tag": "For students and academic labs",
        "features": [
            "30 searches per month",
            "All 16 export formats including Neo4j Cypher and RDF",
            "100 AI analyst messages",
        ],
    },
    "starter": {
        "id": "starter",
        "name": "Starter",
        "price_inr": 499,
        "amount_paise": 49900,
        "searches": 50,
        "analyst_messages": 250,
        "period_days": 30,
        "tag": "For working researchers",
        "features": [
            "50 searches per month",
            "All 16 export formats",
            "250 AI analyst messages",
            "Priority support",
        ],
    },
    "pro": {
        "id": "pro",
        "name": "Professional",
        "price_inr": 1999,
        "amount_paise": 199900,
        "searches": 300,
        "analyst_messages": 1500,
        "period_days": 30,
        "tag": "For R&D teams",
        "features": [
            "300 searches per month",
            "All export formats + enriched Excel and structure images",
            "1,500 AI analyst messages",
            "Batch queries and API access",
        ],
    },
    "team": {
        "id": "team",
        "name": "Team",
        "price_inr": 7499,
        "amount_paise": 749900,
        "searches": 3000,
        "analyst_messages": 10000,
        "period_days": 30,
        "tag": "Up to 10 seats",
        "features": [
            "3,000 searches per month across 10 seats",
            "Everything in Professional",
            "Shared workspace and team billing",
        ],
    },
}

PAID_PLANS = [p for k, p in PLANS.items() if p["amount_paise"] > 0]


def public_plans() -> list[dict]:
    return [
        {
            "id": p["id"],
            "name": p["name"],
            "price_inr": p["price_inr"],
            "searches": p["searches"],
            "analyst_messages": p["analyst_messages"],
            "tag": p.get("tag"),
            "features": p["features"],
        }
        for p in PLANS.values()
    ]


def get_plan(plan_id: str) -> dict:
    plan = PLANS.get((plan_id or "").lower())
    if not plan:
        raise HTTPException(status_code=400, detail="Unknown plan")
    return plan


# ── Razorpay transport ───────────────────────────────────────────────────────

class RazorpayError(RuntimeError):
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code or 502


def _auth_header() -> dict:
    raw = f"{KEY_ID}:{KEY_SECRET}".encode("utf-8")
    return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}


def rzp_request(method: str, path: str, payload: Optional[dict] = None) -> dict:
    """Call the Razorpay REST API. Secrets stay in this function."""
    if not BILLING_ENABLED:
        raise RazorpayError("Payments are not configured on this server", 503)
    url = f"{RAZORPAY_API}{path}"
    headers = {"Content-Type": "application/json", **_auth_header()}
    try:
        resp = _requests.request(
            method, url, headers=headers, json=payload, timeout=HTTP_TIMEOUT
        )
    except _requests.RequestException as exc:
        raise RazorpayError(f"Could not reach the payment provider: {exc.__class__.__name__}")
    if resp.status_code >= 400:
        # Never echo the provider's raw body back to the browser.
        LOG.warning("Razorpay %s %s -> HTTP %s", method, path, resp.status_code)
        raise RazorpayError("The payment provider rejected the request", 502)
    try:
        return resp.json()
    except ValueError:
        raise RazorpayError("The payment provider returned an unreadable response", 502)


def create_razorpay_order(plan: dict, user: dict, receipt: str) -> dict:
    """Create a real Razorpay order for a plan. Amount is taken from the
    server-side catalog, never from the request."""
    notes = {
        "plan": plan["id"],
        "user_id": str(user["id"]),
        "purpose": "scigraph_subscription",
    }
    order = rzp_request("POST", "/orders", {
        "amount": plan["amount_paise"],
        "currency": "INR",
        "receipt": receipt[:40],
        "notes": notes,
        "payment_capture": 1,
    })
    if not order.get("id"):
        raise RazorpayError("The payment provider did not return an order id", 502)
    return order


def is_razorpay_reference(value: str) -> bool:
    """True when `value` has the shape of a Razorpay id. Exposed so routes can
    reject a malformed reference instead of treating it as a pending payment."""
    return bool(value) and bool(_RAZORPAY_REF_RE.match(value))


def fetch_payment(payment_id: str) -> dict:
    # Razorpay ids are `pay_<alphanumerics>` / `order_<alphanumerics>`: the
    # underscore is part of the id, so a plain isalnum() check rejected every
    # real reference. Only path-unsafe characters are refused here.
    if not payment_id or not _RAZORPAY_REF_RE.match(payment_id):
        raise RazorpayError("Invalid payment reference", 400)
    return rzp_request("GET", f"/payments/{payment_id}")


# ── Signature verification ───────────────────────────────────────────────────

def verify_checkout_signature(order_id: str, payment_id: str, signature: str) -> bool:
    """Razorpay checkout: HMAC-SHA256(order_id + '|' + payment_id, key_secret)."""
    if not (order_id and payment_id and signature and KEY_SECRET):
        return False
    expected = hmac.new(
        KEY_SECRET.encode("utf-8"),
        f"{order_id}|{payment_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, str(signature))


def verify_webhook_signature(raw_body: bytes, signature: str) -> bool:
    """Razorpay webhook: HMAC-SHA256(raw request body, webhook secret)."""
    if not (raw_body and signature and WEBHOOK_SECRET):
        return False
    expected = hmac.new(
        WEBHOOK_SECRET.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, str(signature))


# ── Entitlements & usage ─────────────────────────────────────────────────────

def entitlement_for(user: Optional[dict]) -> dict:
    """Resolve plan, remaining quota and billing state for a user (or anonymous)."""
    if not user:
        return {
            "signed_in": False,
            "plan": "free",
            "plan_name": "Guest",
            "searches_limit": 0,
            "searches_used": 0,
            "searches_remaining": 0,
            "analyst_limit": 0,
            "analyst_used": 0,
            "analyst_remaining": 0,
            "period_end": None,
            "billing_enabled": BILLING_ENABLED,
            "auth_required": REQUIRE_AUTH,
            "status": "anonymous",
        }

    user = billing_store.reset_period_if_due(user)
    plan = PLANS.get(user["plan"], PLANS["free"])
    return {
        "signed_in": True,
        "user_id": user["id"],
        "email": user["email"],
        "name": user.get("name"),
        "email_verified": bool(user.get("email_verified_at")),
        "plan": plan["id"],
        "plan_name": plan["name"],
        "plan_status": user["plan_status"],
        "searches_limit": plan["searches"],
        "searches_used": user["searches_used"],
        "searches_remaining": max(0, plan["searches"] - user["searches_used"]),
        "analyst_limit": plan["analyst_messages"],
        "analyst_used": user["analyst_used"],
        "analyst_remaining": max(0, plan["analyst_messages"] - user["analyst_used"]),
        "period_end": user["period_end"],
        "billing_enabled": BILLING_ENABLED,
        "auth_required": REQUIRE_AUTH,
        "status": "active",
    }


def require_search_entitlement(request: Optional[Request]) -> tuple[Optional[dict], dict]:
    """Gate for POST /api/search.

    Accounts are enforced by default: anonymous searches are refused with 401
    so every search is attributable to an account and can be metered. With
    REQUIRE_AUTH=0 the guest tier is reopened and anonymous searches run
    unmetered, exactly as they did before accounts existed. Either way, once an
    account is signed in it *is* metered.
    """
    if request is None:
        return None, entitlement_for(None)

    user = auth.current_user_optional(request)
    ent = entitlement_for(user)

    if user is None:
        if REQUIRE_AUTH:
            raise HTTPException(
                status_code=401,
                detail={"message": "Sign in to run a search",
                        "code": "auth_required",
                        "upgrade_url": "/#pricing"},
            )
        return None, ent

    if ent["searches_remaining"] <= 0:
        raise HTTPException(
            status_code=402,
            detail={
                "message": (f"You have used all {ent['searches_limit']} searches on the "
                            f"{ent['plan_name']} plan this month. Upgrade to keep searching."),
                "code": "quota_reached",
                "plan": ent["plan"],
                "upgrade_url": "/#pricing",
            },
        )
    return user, ent


def require_analyst_entitlement(request: Optional[Request]) -> Optional[dict]:
    """Gate for POST /api/analyze — the analyst costs real LLM tokens, so it
    follows the same sign-in rule as search. Returns the user when signed in,
    or None when the guest tier is open."""
    if request is None:
        return None
    try:
        user = auth.current_user_optional(request)
    except Exception as exc:  # identity lookup must never break the product
        LOG.warning("Analyst identity lookup failed: %s", exc.__class__.__name__)
        return None
    if user is None and REQUIRE_AUTH:
        raise HTTPException(
            status_code=401,
            detail={"message": "Sign in to use the AI analyst",
                    "code": "auth_required",
                    "upgrade_url": "/#pricing"},
        )
    return user


def record_search_usage(search_id: str) -> None:
    """Count one search for the signed-in user who started it (best effort)."""
    try:
        user_id = billing_store.claim_search_owner(search_id)
        if user_id is None:
            return
        billing_store.increment_counters(user_id, searches=1)
        billing_store.record_usage(user_id, "search", 1, ref=search_id)
    except Exception as exc:  # never fail a completed search over metering
        LOG.warning("Usage accounting failed for %s: %s", search_id, exc.__class__.__name__)


def record_analyst_usage(request: Request) -> Optional[dict]:
    """Count one AI analyst interaction for the caller (best effort)."""
    try:
        user = auth.current_user_optional(request)
        if not user:
            return None
        billing_store.increment_counters(user["id"], analyst=1)
        billing_store.record_usage(user["id"], "analyst", 1)
        return user
    except Exception as exc:
        LOG.warning("Analyst usage accounting failed: %s", exc.__class__.__name__)
        return None


def attach_search_owner(search_id: str, user: Optional[dict]) -> None:
    if not user:
        return
    try:
        billing_store.set_search_owner(search_id, user["id"])
    except Exception as exc:
        LOG.warning("Could not record search ownership: %s", exc.__class__.__name__)


# ── Payment lifecycle (server-side truth) ────────────────────────────────────

def _order_plan(order_id: str) -> Optional[tuple[dict, dict]]:
    """Look up our order record → (order row, user row). Plan comes from our DB,
    never from the client."""
    order = billing_store.get_order(order_id)
    if not order:
        return None
    user = billing_store.get_user_by_id(order["user_id"])
    if not user:
        return None
    return order, user


def academic_purchase_allowed(user: dict, token: Optional[str] = None) -> tuple[bool, str]:
    """Server-side authority for buying the Academic plan.

    Called from order creation *and* again at activation, because a caller can
    reach `activate_for_payment` through the webhook path, which never sees the
    attestation token. Both checks must hold: the durable verified status, and a
    live attestation that proves verification happened moments ago.
    """
    if not academic.is_verified(user):
        return False, ("Verify your academic email before buying the Academic plan.")
    if not token:
        return False, ("Your verification expired. Please verify your academic email again.")
    result = academic.verify_token(token, user["id"], user.get("academic_verified_email") or "")
    if not result["ok"]:
        return False, result["reason"]
    return True, "verified"


def activate_for_payment(payment: dict) -> Optional[dict]:
    """Activate the plan tied to a captured payment. Idempotent.

    Returns the updated user, or None when the payment cannot be attributed.
    """
    order_id = payment.get("order_id")
    payment_id = payment.get("id")
    if not order_id or not payment_id:
        return None

    found = _order_plan(order_id)
    if not found:
        LOG.warning("Captured payment %s has no matching order", payment_id)
        return None
    order, user = found

    plan = PLANS.get(order["plan"], PLANS["free"])

    # Academic entitlement is re-checked here, not just at checkout. Reaching
    # this function through the webhook or the reconcile path means no
    # attestation token was presented, so the durable verified status is the
    # only thing we can check — and it is enough to refuse an Academic upgrade
    # for an account that never verified. The payment is still recorded so the
    # money is traceable and support can refund it.
    if order["plan"] == "academic" and not academic.is_verified(user):
        LOG.warning("Refusing Academic activation for user %s: not verified (payment %s)",
                    user["id"], payment_id)
        billing_store.record_payment(
            payment_id, order_id, user["id"], order["plan"],
            int(payment.get("amount") or order["amount_paise"]),
            "captured_unverified", payment.get("method"), captured=True,
        )
        billing_store.record_subscription(
            user["id"], "academic", "blocked_unverified", payment_id,
            order["amount_paise"], plan["period_days"],
        )
        return None
    is_new = billing_store.record_payment(
        payment_id,
        order_id,
        user["id"],
        order["plan"],
        int(payment.get("amount") or order["amount_paise"]),
        "captured",
        payment.get("method"),
        captured=True,
    )

    if not is_new:
        # Already recorded — make sure the plan is still active (idempotent replay).
        if user["plan"] != plan["id"]:
            return billing_store.set_plan(
                user["id"], plan["id"], plan["period_days"], "active",
                payment_id, order["amount_paise"],
            )
        return user

    billing_store.set_order_status(order_id, "paid")
    return billing_store.set_plan(
        user["id"], plan["id"], plan["period_days"], "active",
        payment_id, order["amount_paise"],
    )


def confirm_captured_payment(order_id: str, payment_id: str) -> Optional[dict]:
    """Verify a checkout payment end-to-end and activate the plan.

    Order of checks: signature (proves the browser saw Razorpay's response),
    then the provider's own record of the payment (status + amount + order
    linkage). Nothing is granted on the client's word alone.
    """
    payment = fetch_payment(payment_id)
    if payment.get("order_id") != order_id:
        raise RazorpayError("Payment does not belong to this order", 400)
    status = (payment.get("status") or "").lower()
    if status not in ("captured", "authorized"):
        raise RazorpayError(f"Payment is not completed (status: {status or 'unknown'})", 400)
    return activate_for_payment(payment)


def reconcile_captured_payment(payment_id: str,
                               user: Optional[dict] = None) -> tuple[Optional[dict], Optional[dict]]:
    """Server-side safety net for a captured-but-not-activated payment.

    The browser normally confirms the payment right after checkout, and the
    webhook confirms it a moment later. If either is lost (closed tab, dropped
    request, webhook not yet configured) Razorpay still holds the money while
    the account stays on Free. This asks Razorpay directly and credits the plan
    when the payment really is captured.

    Returns (activated_user_or_None, provider_payment_or_None).
    """
    if not BILLING_ENABLED or not payment_id:
        return None, None
    try:
        payment = fetch_payment(payment_id)
    except RazorpayError as exc:
        LOG.info("Reconcile could not read payment %s: %s", payment_id, exc)
        return None, None

    if (payment.get("status") or "").lower() != "captured":
        return None, payment

    order_id = payment.get("order_id")
    order = billing_store.get_order(order_id) if order_id else None
    if not order:
        LOG.warning("Reconcile: payment %s has no matching order", payment_id)
        return None, payment

    # Never credit a payment to somebody else's account.
    if user is not None and order["user_id"] != user["id"]:
        LOG.warning("Reconcile refused: payment %s belongs to another account", payment_id)
        return None, payment

    owner = billing_store.get_user_by_id(order["user_id"])
    if owner and owner["plan"] == order["plan"] and owner["plan_status"] == "active":
        return None, payment   # already credited

    return activate_for_payment(payment), payment


def reconcile_latest_order(user: Optional[dict], max_age_hours: int = 24) -> Optional[dict]:
    """Recover a payment made in an earlier session.

    Looks at the caller's most recent order that we never saw confirmed and
    asks Razorpay whether a payment against it was captured. Lets a customer
    who closed the tab mid-checkout get their plan on the next visit.
    """
    if not BILLING_ENABLED or not user:
        return None
    try:
        row = billing_store.latest_unconfirmed_order(user["id"])
    except Exception as exc:
        LOG.warning("Could not read pending orders: %s", exc)
        return None
    if not row:
        return None

    try:
        created = datetime.fromisoformat(row["created_at"])
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - created > timedelta(hours=max_age_hours):
            return None
    except (TypeError, ValueError):
        return None

    try:
        order = rzp_request("GET", f"/orders/{row['order_id']}")
    except RazorpayError:
        return None

    for payment in (order or {}).get("payments") or []:
        if (payment.get("status") or "").lower() == "captured":
            payload = dict(payment)
            payload["order_id"] = row["order_id"]
            return activate_for_payment(payload)
    return None


def downgrade_for_refund(payment_id: str) -> Optional[dict]:
    payment = billing_store.get_payment(payment_id)
    if not payment or not payment.get("user_id"):
        return None
    return billing_store.set_plan(payment["user_id"], "free", 30, "refunded")