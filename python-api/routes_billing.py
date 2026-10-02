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
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel

import auth
import billing
import billing_store
import db as _db
import supabase_auth

LOG = logging.getLogger("scigraph.billing.api")

# Shared secret for the Academic directory. Unset => the endpoint is disabled
# and answers 404, so an unconfigured deployment exposes no customer data.
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "").strip()


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