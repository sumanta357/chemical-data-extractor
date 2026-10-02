#!/usr/bin/env python3
"""
Transactional email for account verification and password reset.

Delivery is a single HTTPS call to Resend, so the container gains no new
dependency (requests is already required). Every function fails soft: a
missing API key or a provider outage logs a warning and returns False, and
never turns into a 500 on the signup or reset endpoint. That matters because
the account must still be creatable when the mail provider is down — the
token stays valid and the user can request a new link.

Env
---
RESEND_API_KEY   API token. Unset => delivery is skipped, links are logged.
EMAIL_FROM       verified sender, e.g. "SciGraph <no-reply@yourdomain.com>"
APP_URL          public origin, used to build the links in the emails
"""

import logging
import os

import requests

LOG = logging.getLogger("scigraph.email")

RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "").strip()
EMAIL_FROM = os.environ.get("EMAIL_FROM", "SciGraph <onboarding@resend.dev>").strip()
APP_URL = os.environ.get("APP_URL", "").strip().rstrip("/")
RESEND_ENDPOINT = "https://api.resend.com/emails"
HTTP_TIMEOUT = 10

EMAIL_ENABLED = bool(RESEND_API_KEY)

# Local-development escape hatch: returns the link in the API response so the
# flow can be exercised without a mail provider. Never enable in production.
EXPOSE_LINKS = os.environ.get("EMAIL_EXPOSE_LINKS", "0") in ("1", "true", "yes", "on")


def _link(path: str, token: str) -> str:
    base = APP_URL or "http://localhost:8000"
    return f"{base}{path}?token={token}"


def send(to: str, subject: str, html: str, text: str) -> bool:
    """Best-effort send. Returns True only when the provider accepted it."""
    if not RESEND_API_KEY:
        LOG.warning("RESEND_API_KEY is not set — email not delivered to %s", to)
        return False
    try:
        resp = requests.post(
            RESEND_ENDPOINT,
            headers={"Authorization": f"Bearer {RESEND_API_KEY}",
                     "Content-Type": "application/json"},
            json={"from": EMAIL_FROM, "to": [to], "subject": subject,
                  "html": html, "text": text},
            timeout=HTTP_TIMEOUT,
        )
    except requests.RequestException as exc:
        LOG.warning("Email delivery failed for %s: %s", to, exc.__class__.__name__)
        return False
    if resp.status_code >= 400:
        # Never echo the provider's body: it can contain the address.
        LOG.warning("Email provider rejected the send to %s (HTTP %s)", to, resp.status_code)
        return False
    return True


def _shell(title: str, body_html: str, cta_label: str, cta_url: str, footnote: str) -> str:
    return (
        "<div style=\"font-family:ui-sans-serif,system-ui,-apple-system,Segoe UI,"
        "Roboto,sans-serif;background:#0a0f1a;padding:32px 16px\">"
        "<div style=\"max-width:520px;margin:0 auto;background:#111827;"
        "border:1px solid #1f2937;padding:28px\">"
        f"<h1 style=\"color:#e5e7eb;font-size:18px;margin:0 0 12px\">{title}</h1>"
        f"<p style=\"color:#9ca3af;font-size:14px;line-height:1.6\">{body_html}</p>"
        "<p style=\"margin:24px 0\">"
        f"<a href=\"{cta_url}\" style=\"background:#22d3ee;color:#06202a;"
        "padding:11px 20px;text-decoration:none;font-size:14px;font-weight:600;"
        "display:inline-block\">{cta_label}</a></p>"
        f"<p style=\"color:#6b7280;font-size:12px;line-height:1.6\">{footnote}</p>"
        "</div></div>"
    )


def send_verification(to: str, token: str) -> bool:
    url = _link("/verify-email", token)
    delivered = send(
        to,
        "Confirm your SciGraph account",
        _shell("Confirm your email",
               "Confirm this address to secure your account. This link works once "
               "and expires in 24 hours.",
               "Confirm email address", url,
               "If you did not create this account you can ignore this message."),
        f"Confirm your SciGraph account: {url}\n\nThis link works once and "
        f"expires in 24 hours. If you did not create this account, ignore this email.",
    )
    if not delivered:
        LOG.info("Verification link for %s: %s", to, url)
    return delivered


def send_password_reset(to: str, token: str) -> bool:
    url = _link("/reset-password", token)
    delivered = send(
        to,
        "Reset your SciGraph password",
        _shell("Reset your password",
               "Use the button below to choose a new password. The link works once "
               "and expires in 60 minutes, and every other signed-in device is "
               "signed out once it is used.",
               "Choose a new password", url,
               "If you did not request this, no action is needed — your password "
               "has not changed."),
        f"Reset your SciGraph password: {url}\n\nThis link works once and expires "
        f"in 60 minutes. If you did not request this, ignore this email.",
    )
    if not delivered:
        LOG.info("Password reset link for %s: %s", to, url)
    return delivered
