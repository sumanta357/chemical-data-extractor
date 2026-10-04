#!/usr/bin/env python3
"""
Account + billing HTTP endpoints.

Mounted from app.py BEFORE the SPA catch-all route so these paths are matched
as API routes (never as the static index.html).
"""

import json
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel

import auth
import academic
import billing
import billing_store
import db as _db
import supabase_auth

LOG = logging.getLogger("scigraph.billing.api")

# Shared secret for the Academic directory. Unset => the endpoint is disabled
# and answers 404, so an unconfigured deployment exposes no customer data.
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "").strip()


def _iso_utc(timestamp: float) -> str:
    """UTC ISO string, matching how billing_store writes timestamps so the
    comparisons in SQL compare like with like."""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _require_admin(request: Request) -> None:
    if not ADMIN_TOKEN:
        raise HTTPException(status_code=404, detail="Not found")
    supplied = (request.headers.get("x-admin-token")
                or request.query_params.get("token") or "")
    if not supplied or not secrets.compare_digest(supplied, ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="Admin token required")

router = APIRouter(tags=["accounts", "billing"])


# ── Request models ───────────────────────────────────────────────────────────

class ProfileRequest(BaseModel):
    name: Optional[str] = None
    academic_email: Optional[str] = None
    institute_name: Optional[str] = None
    institute_id: Optional[str] = None
    phone: Optional[str] = None
    academician_type: Optional[str] = None


class CreateOrderRequest(BaseModel):
    plan: str
    # Short-lived attestation from POST /api/account/academic/verify. Required
    # for the Academic plan; ignored for the others.
    academic_verification_token: Optional[str] = None


class AcademicVerifyRequest(BaseModel):
    academic_email: str
    institute_name: Optional[str] = None
    institute_id: Optional[str] = None
    academician_type: Optional[str] = None


class VerifyPaymentRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


# ── Public configuration ─────────────────────────────────────────────────────

@router.get("/api/billing/config")
async def billing_config():
    """Everything the browser needs to run checkout. Only the publishable key
    id is exposed; the key secret never leaves the server.

    Also reports whether the accounts store is actually reachable, so a
    misconfigured DATABASE_URL is visible without digging through logs.
    """
    store = _db.status()
    return {
        "billing_enabled": billing.BILLING_ENABLED,
        "key_id": billing.KEY_ID if billing.BILLING_ENABLED else None,
        "currency": "INR",
        "auth_required": billing.REQUIRE_AUTH,
        "checkout_script": "https://checkout.razorpay.com/v1/checkout.js",
        "accounts_store": store,
        "admin_directory": bool(ADMIN_TOKEN),
        # Supabase Auth: the browser signs in with the project URL and the
        # anon/publishable key. Both are public by design — the server uses the
        # same anon key to verify the access token it is handed. The
        # service_role key is never exposed, and is not needed.
        "supabase": {
            "url": supabase_auth.SUPABASE_URL or None,
            "anon_key": supabase_auth.SUPABASE_ANON_KEY or None,
            "configured": supabase_auth.SUPABASE_ENABLED,
            # Which providers the project actually has enabled. The UI hides
            # buttons for anything turned off, so nobody clicks into a
            # "provider is not enabled" error.
            "providers": supabase_auth.auth_providers(),
        },
    }


@router.get("/api/billing/plans")
async def list_plans():
    return {"plans": billing.public_plans()}


# ── Accounts ─────────────────────────────────────────────────────────────────

@router.get("/api/account/me")
async def me(request: Request):
    """Always 200 — the UI needs to know it is anonymous, not to fail.

    Best effort: if this account has an order that was paid for but never
    confirmed (closed tab, lost request), credit it before answering.
    """
    user = auth.current_user_optional(request)
    if user:
        try:
            activated = billing.reconcile_latest_order(user)
            if activated:
                user = activated
        except Exception as exc:  # never fail the session check over this
            LOG.info("Order reconciliation skipped: %s", exc.__class__.__name__)
    return {
        "user": _public_user(user) if user else None,
        "entitlement": billing.entitlement_for(user),
    }


@router.get("/api/account/usage")
async def usage(request: Request):
    user = auth.current_user_optional(request)
    if not user:
        raise HTTPException(status_code=401, detail="Sign in to continue")
    ent = billing.entitlement_for(user)
    return {
        "entitlement": ent,
        "payments": billing_store.recent_payments(user["id"]),
        "recent_searches": billing_store.recent_usage(user["id"], "search", 50),
    }


def _public_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "email": user["email"],
        "name": user.get("name"),
        "created_at": user["created_at"],
        "email_verified": bool(user.get("email_verified_at")),
    }


# ── Academic profile & directory ─────────────────────────────────────────────

@router.get("/api/account/profile")
async def get_profile(request: Request):
    user = auth.current_user_required(request)
    return {"profile": billing_store.get_profile(user["id"])}


@router.put("/api/account/profile")
async def put_profile(payload: ProfileRequest, request: Request):
    user = auth.current_user_required(request)
    fields = auth.validate_profile(payload.model_dump())
    return {"profile": billing_store.update_profile(user["id"], fields)}


# ── Academic plan verification ──────────────────────────────────────────────

@router.get("/api/account/academic/status")
async def academic_status(request: Request):
    """Current verification state, for rendering the checkout gate."""
    user = auth.current_user_required(request)
    return {
        "verification": academic.status_for(user),
        "profile": billing_store.get_profile(user["id"]),
    }


class AcademicEligibilityRequest(BaseModel):
    email: str


@router.post("/api/account/academic/eligibility")
async def academic_eligibility(payload: AcademicEligibilityRequest, request: Request):
    """Would this address pass the academic check? Read-only.

    The verification endpoint is not a probe: it records an attempt against a
    small hourly budget and flips the account to "rejected" when the domain
    fails. The account panel needs to know whether to show the verification
    form *before* the user commits to it, and asking a personal address such as
    gmail.com should cost neither an attempt nor a rejected status.

    Nothing here is an authorisation — only `academic.classify_email`, the same
    function the real check uses, so the answer cannot drift from the rule that
    will actually be enforced.
    """
    user = auth.current_user_required(request)
    try:
        email = auth.validate_email(payload.email)
    except HTTPException:
        return {"eligible": False, "domain": "", "reason": "That does not look like a valid email address."}

    verdict = academic.classify_email(email)
    LOG.info("Academic eligibility probed for user=%s domain=%s eligible=%s",
             user["id"], verdict["domain"], verdict["eligible"])
    return {
        "eligible": verdict["eligible"],
        "domain": verdict["domain"],
        "reason": verdict["reason"],
    }


@router.post("/api/account/academic/verify")
async def verify_academic(payload: AcademicVerifyRequest, request: Request):
    """Run the eligibility check and, when it passes, mark the account verified.

    Rate limited per account so this cannot be used to enumerate which domains
    are accepted by hammering the endpoint.
    """
    user = auth.current_user_required(request)

    window_start = (time.time() - 3600)
    try:
        recent = billing_store.count_academic_attempts_since(
            user["id"], _iso_utc(window_start)
        )
    except Exception as exc:
        LOG.warning("Academic attempt count failed (%s) — allowing", exc.__class__.__name__)
        recent = 0
    if recent >= academic.MAX_ATTEMPTS_PER_HOUR:
        raise HTTPException(
            status_code=429,
            detail={"message": "Too many verification attempts. Try again in an hour.",
                    "code": "academic_rate_limited"},
        )

    try:
        email = auth.validate_email(payload.academic_email)
    except HTTPException:
        raise HTTPException(status_code=400,
                            detail={"message": "Enter a valid academic email address.",
                                    "code": "academic_invalid_email"})

    verdict = academic.classify_email(email)
    billing_store.record_academic_attempt(
        user["id"], email, verdict["domain"], verdict["eligible"], verdict["reason"]
    )

    if not verdict["eligible"]:
        billing_store.set_academic_status(user["id"], "rejected")
        raise HTTPException(
            status_code=422,
            detail={"message": verdict["reason"],
                    "code": "academic_not_eligible",
                    "domain": verdict["domain"]},
        )

    if billing_store.academic_email_claimed_by_other(email, user["id"]):
        raise HTTPException(
            status_code=409,
            detail={"message": "That academic email is already linked to another account.",
                    "code": "academic_already_claimed"},
        )

    # Persist the institutional details alongside the verification so the
    # academic directory and the refund trail agree with what was checked.
    fields = auth.validate_profile({
        "academic_email": email,
        "institute_name": payload.institute_name,
        "institute_id": payload.institute_id,
        "academician_type": payload.academician_type,
    })
    billing_store.update_profile(user["id"], fields)
    updated = billing_store.set_academic_status(user["id"], "verified", email)

    token = academic.issue_token(user["id"], email)
    if not token:
        # Refuse rather than hand back an unverifiable purchase.
        billing_store.set_academic_status(user["id"], "none")
        raise HTTPException(
            status_code=503,
            detail={"message": "Academic verification is not configured on this server.",
                    "code": "academic_verification_unavailable"},
        )

    return {
        "status": "verified",
        "message": f"{verdict['domain']} is an eligible academic institution.",
        "verification": academic.status_for(updated),
        "token": token,
        "expires_in": academic.TOKEN_TTL_SECONDS,
    }


@router.get("/api/admin/academics")
async def list_academics(
    request: Request,
    q: Optional[str] = Query(None),
    plan: str = Query("academic"),
    academic_email: Optional[str] = Query(None),
    institute_name: Optional[str] = Query(None),
    institute_id: Optional[str] = Query(None),
    phone: Optional[str] = Query(None),
    academician_type: Optional[str] = Query(None),
    verified: Optional[bool] = Query(None),
    sort: str = Query("created_at"),
    order: str = Query("desc"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """Sortable, filterable list of academic customers.

    Guarded by a shared admin token; `sort` is whitelisted in the store and
    every filter is a bound parameter."""
    _require_admin(request)
    result = billing_store.list_academic_customers(
        plan=plan or "academic", q=q, academic_email=academic_email,
        institute_name=institute_name, institute_id=institute_id, phone=phone,
        academician_type=academician_type, verified=verified, sort=sort,
        order=order, limit=limit, offset=offset,
    )
    result["academician_types"] = billing_store.ACADEMICIAN_TYPES
    return result


# ── Razorpay: order creation ─────────────────────────────────────────────────

@router.post("/api/billing/create-order")
async def create_order(payload: CreateOrderRequest, request: Request):
    user = auth.current_user_required(request)
    if not billing.BILLING_ENABLED:
        raise HTTPException(status_code=503,
                            detail="Payments are not configured on this server yet")

    plan = billing.get_plan(payload.plan)
    if plan["amount_paise"] <= 0:
        raise HTTPException(status_code=400, detail="The free plan does not require payment")

    # Academic eligibility is enforced here, on the server, before any money
    # moves. A caller who skips the UI and posts straight to this endpoint with
    # plan='academic' gets the same refusal as the UI shows.
    if plan["id"] == "academic":
        allowed, reason = billing.academic_purchase_allowed(
            user, payload.academic_verification_token
        )
        if not allowed:
            LOG.info("Blocked Academic checkout for user %s: %s", user["id"], reason)
            raise HTTPException(
                status_code=403,
                detail={"message": reason,
                        "code": "academic_verification_required"},
            )

    receipt = f"scigraph_{user['id']}_{int(time.time())}"
    try:
        order = billing.create_razorpay_order(plan, user, receipt)
    except billing.RazorpayError as exc:
        LOG.warning("Order creation failed for plan %s: %s", plan["id"], exc)
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc))

    billing_store.create_order(
        order["id"], user["id"], plan["id"], int(order.get("amount", plan["amount_paise"])),
        receipt,
    )
    return {
        "order_id": order["id"],
        "amount": int(order.get("amount", plan["amount_paise"])),
        "currency": order.get("currency", "INR"),
        "key_id": billing.KEY_ID,
        "plan": plan["id"],
        "plan_name": plan["name"],
        "receipt": receipt,
    }


# ── Razorpay: verification after checkout ────────────────────────────────────

@router.post("/api/billing/verify-payment")
async def verify_payment(payload: VerifyPaymentRequest, request: Request):
    user = auth.current_user_required(request)

    if not billing.verify_checkout_signature(
        payload.razorpay_order_id, payload.razorpay_payment_id, payload.razorpay_signature
    ):
        LOG.warning("Rejected payment verification for user %s (bad signature)", user["id"])
        raise HTTPException(status_code=400, detail="Payment verification failed")

    order = billing_store.get_order(payload.razorpay_order_id)
    if not order or order["user_id"] != user["id"]:
        raise HTTPException(status_code=400, detail="This order does not belong to your account")

    try:
        updated = billing.confirm_captured_payment(
            payload.razorpay_order_id, payload.razorpay_payment_id
        )
    except billing.RazorpayError as exc:
        raise HTTPException(status_code=exc.status_code or 502, detail=str(exc))

    if not updated:
        raise HTTPException(status_code=400, detail="Could not match this payment to a plan")

    return {
        "status": "active",
        "message": f"{billing.PLANS[updated['plan']]['name']} plan is active.",
        "entitlement": billing.entitlement_for(updated),
    }


@router.get("/api/billing/payment-status/{payment_id}")
async def payment_status(payment_id: str, request: Request):
    """Payment progress for the UI, and a reconciliation point.

    If Razorpay reports the payment as captured but we never activated the
    plan (the browser's verify call was lost, or the webhook is not set up
    yet), this credits it now — the plan is only ever granted after asking
    Razorpay, never on the browser's word.
    """
    user = auth.current_user_required(request)
    # Reject a malformed reference up front instead of reporting it as a
    # "pending" payment. The value is parameterised everywhere it is used, so
    # this is about an honest API rather than an injection defence.
    if not billing.is_razorpay_reference(payment_id):
        raise HTTPException(status_code=400, detail="Invalid payment reference")
    record = billing_store.get_payment(payment_id)

    if record and record.get("user_id") and record["user_id"] != user["id"]:
        raise HTTPException(status_code=403, detail="Not your payment")

    activated, provider_payment = billing.reconcile_captured_payment(payment_id, user)
    provider_status = (provider_payment or {}).get("status")
    if activated:
        record = billing_store.get_payment(payment_id)

    ent = billing.entitlement_for(activated or user)
    return {
        "payment_id": payment_id,
        "recorded": bool(record),
        "status": (record or {}).get("status") or provider_status or "pending",
        "plan": (record or {}).get("plan") or (ent["plan"] if provider_status == "captured" else None),
        "amount_paise": (record or {}).get("amount_paise"),
        "entitlement": ent,
    }


# ── Razorpay: webhooks (source of truth for the plan) ────────────────────────

@router.post("/api/billing/webhook")
async def webhook(request: Request):
    raw = await request.body()
    signature = request.headers.get("x-razorpay-signature", "")

    if not billing.verify_webhook_signature(raw, signature):
        LOG.warning("Rejected webhook with an invalid signature")
        raise HTTPException(status_code=400, detail="Invalid signature")

    try:
        event = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid payload")

    event_type = event.get("event", "")
    payload_entity = (event.get("payload") or {}).get("payment", {}).get("entity", {})

    if event_type == "payment.captured" and payload_entity.get("id"):
        payment_id = payload_entity["id"]
        if billing_store.claim_webhook_event(f"payment.captured:{payment_id}", event_type):
            billing.activate_for_payment(payload_entity)

    elif event_type == "payment.failed" and payload_entity.get("id"):
        payment_id = payload_entity["id"]
        if billing_store.claim_webhook_event(f"payment.failed:{payment_id}", event_type):
            billing_store.record_payment(
                payment_id,
                payload_entity.get("order_id"),
                None,
                None,
                payload_entity.get("amount"),
                "failed",
                payload_entity.get("method"),
            )

    elif event_type == "refund.processed":
        refund = (event.get("payload") or {}).get("refund", {}).get("entity", {})
        payment_id = refund.get("payment_id")
        if payment_id and billing_store.claim_webhook_event(
            f"refund.processed:{refund.get('id', payment_id)}", event_type
        ):
            billing.downgrade_for_refund(payment_id)

    return {"status": "ok"}