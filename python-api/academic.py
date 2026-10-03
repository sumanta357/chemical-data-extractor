#!/usr/bin/env python3
"""
Academic eligibility verification for the Academic plan.

Before this module existed the Academic plan was an ordinary paid plan: any
signed-in visitor could buy it by typing an academic email into their profile,
and nothing on the server checked that the address belonged to an academic
institution. The frontend cannot enforce this — a caller can always POST to
`/api/billing/create-order` directly — so every rule here is enforced on the
server and re-checked again at activation time.

Two separate things are tracked, deliberately:

  * `academic_status = 'verified'` is *durable*. It survives until the academic
    address on the profile changes (which resets it) or an operator revokes it.
    It is what the payment layer trusts.
  * The attestation token is *short-lived* (default 30 minutes). It proves a
    verification was performed moments ago, so a stale token captured from a
    request log cannot be replayed later to start a fresh checkout.

Scope note: this verifies the address is *institutional* — it cannot prove the
requester controls the mailbox. Proving that needs a confirmation link, which
needs SMTP (see docs/BILLING.md). Until SMTP is configured this is a domain
eligibility check, and `mailbox_proof_required` is reported as `false` so the
UI never implies more assurance than we actually have.
"""

import base64
import fnmatch
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Optional, Tuple

LOG = logging.getLogger("scigraph.academic")

# Refuse anything that is not an institutional address. Consumer mailbox
# providers are listed explicitly rather than relying on the suffix check,
# because the suffix check alone would wave through `student@gmail.com`.
CONSUMER_DOMAINS = {
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "yahoo.co.uk",
    "ymail.com", "hotmail.com", "outlook.com", "live.com", "msn.com",
    "aol.com", "icloud.com", "me.com", "mac.com", "proton.me", "protonmail.com",
    "gmx.com", "gmx.de", "mail.com", "zoho.com", "yandex.com", "yandex.ru",
    "mail.ru", "qq.com", "163.com", "126.com", "naver.com", "rediffmail.com",
    "sify.com", "indiatimes.com", "tutanota.com", "fastmail.com", "hey.com",
    "duck.com", "example.com", "example.org", "example.net", "localhost",
    # Consumer providers on country TLDs, checked before any allowlist so an
    # operator adding ".ca" still cannot wave these through.
    "yahoo.ca", "sympatico.ca", "rogers.ca", "shaw.ca", "telus.net",
    "btinternet.com", "sky.com", "orange.fr", "wanadoo.fr", "libero.it",
    "t-online.de", "seznam.cz", "wp.pl", "o2.pl", "nate.com", "hanmail.net",
}

# Institution domain endings we accept outright.
ELIGIBLE_SUFFIXES = (
    ".edu", ".edu.in", ".ac.in", ".ac.uk", ".ac.jp", ".ac.cn", ".ac.za",
    ".ac.au", ".edu.au", ".edu.cn", ".edu.sg", ".edu.my", ".edu.pk", ".edu.br",
    ".ac.ir", ".ac.th", ".ac.kr", ".ac.id", ".ac.il", ".ac.be", ".ac.at",
    ".ac.de", ".ac.es", ".ac.fr", ".ac.nl", ".ac.se", ".ac.no", ".ac.dk",
    ".ac.fi", ".ac.ch", ".ac.pl", ".ac.cz", ".ac.gr", ".ac.pt", ".ac.it",
    ".uni-*.de", ".univ-*.fr", ".g.ac.uk", ".nwu.ac.za", ".uj.ac.za",
)

# Country TLDs that are NOT auto-accepted: `.ca`, `.de`, `.uk`, `.au`, `.in`
# and friends are used by commercial providers and ISPs as well as schools
# (uwaterloo.ca, uni-bonn.de), so matching the TLD would accept `yahoo.ca` and
# `sympatico.ca`. Operators who serve those countries list the institutions
# explicitly in ACADEMIC_ALLOWED_DOMAINS, which is checked instead of this set.
GENERIC_TLD_NOTE = (".ca", ".de", ".uk", ".com", ".net", ".org", ".io", ".co")

# Operator allowlist. When set, only these domains (and their subdomains) are
# eligible — the right switch for a deployment that wants an explicit list.
ALLOWED_DOMAINS = tuple(
    d.strip().lower().lstrip("@")
    for d in os.environ.get("ACADEMIC_ALLOWED_DOMAINS", "").split(",")
    if d.strip()
)

TOKEN_TTL_SECONDS = int(os.environ.get("ACADEMIC_TOKEN_TTL_SECONDS", "1800"))
MAX_ATTEMPTS_PER_HOUR = int(os.environ.get("ACADEMIC_MAX_ATTEMPTS_PER_HOUR", "5"))


def _secret() -> str:
    """Signing key for attestations. Falls back to the Razorpay secret so the
    feature works out of the box, but a dedicated key is strongly preferred so
    rotating one does not invalidate the other."""
    return (os.environ.get("ACADEMIC_VERIFICATION_SECRET", "").strip()
            or os.environ.get("RAZORPAY_KEY_SECRET", "").strip())


def split_domain(email: str) -> str:
    """The domain part of an address, lowercased. '' when there isn't one."""
    if "@" not in (email or ""):
        return ""
    domain = email.rsplit("@", 1)[1].strip().lower().rstrip(".")
    # Defensive: a domain cannot contain whitespace or an @; refuse anything
    # that tries to smuggle SQL-ish text into the stored profile.
    if not domain or len(domain) > 253 or any(c in domain for c in " @'\"\\"):
        return ""
    if not all(c.isalnum() or c in ".-" for c in domain):
        return ""
    return domain


def _suffix_matches(domain: str, suffix: str) -> bool:
    """True when `domain` ends with an eligible suffix.

    Some patterns are globs (`.uni-*.de`), so a plain endswith() would never
    match them and the domain would be wrongly refused.
    """
    if "*" in suffix or "?" in suffix:
        return fnmatch.fnmatch(domain, "*" + suffix.lstrip("."))
    return domain.endswith(suffix)


def classify_email(email: str) -> dict:
    """Decide whether an address is an eligible academic one.

    Returns a dict rather than raising: the caller needs the reason to show the
    user, and the same shape is stored for the audit trail.
    """
    domain = split_domain(email)
    if not domain:
        return {"eligible": False, "domain": "", "reason": "That does not look like a valid email address."}

    if domain in CONSUMER_DOMAINS:
        return {"eligible": False, "domain": domain,
                "reason": "Use your institution's email address, not a personal one."}

    if ALLOWED_DOMAINS:
        allowed = any(domain == d or domain.endswith("." + d) for d in ALLOWED_DOMAINS)
        if not allowed:
            return {"eligible": False, "domain": domain,
                    "reason": "That institution is not on the list of eligible academic domains."}
        return {"eligible": True, "domain": domain, "reason": "Institution domain accepted."}

    eligible = any(_suffix_matches(domain, s) for s in ELIGIBLE_SUFFIXES)
    if not eligible:
        return {"eligible": False, "domain": domain,
                "reason": "That email domain is not a recognised academic institution."}
    return {"eligible": True, "domain": domain, "reason": "Institution domain accepted."}


# ── Attestation token ────────────────────────────────────────────────────────

def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64d(value: str) -> bytes:
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


def issue_token(user_id: int, academic_email: str, ttl: Optional[int] = None) -> Optional[str]:
    """Sign a short-lived attestation that `user_id` just passed eligibility
    for `academic_email`. Returns None when no signing key is configured, so a
    deployment without one refuses the purchase instead of waving it through."""
    secret = _secret()
    if not secret:
        return None
    now = int(time.time())
    payload = {
        "uid": int(user_id),
        "email": (academic_email or "").strip().lower(),
        "iat": now,
        "exp": now + int(ttl or TOKEN_TTL_SECONDS),
    }
    body = _b64e(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    sig = _b64e(hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def verify_token(token: str, user_id: int, academic_email: str = "") -> dict:
    """Validate an attestation. Returns {ok, reason}. A tampered, expired,
    wrongly-signed or cross-account token all fail the same way."""
    secret = _secret()
    if not secret:
        return {"ok": False, "reason": "verification_unavailable"}
    if not token or not isinstance(token, str) or token.count(".") != 1:
        return {"ok": False, "reason": "Verification is missing. Please verify your academic email again."}

    body, _, sig = token.partition(".")
    expected = _b64e(hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())
    if not hmac.compare_digest(expected, sig):
        return {"ok": False, "reason": "Verification could not be confirmed. Please try again."}

    try:
        payload = json.loads(_b64d(body).decode("utf-8"))
    except Exception:
        return {"ok": False, "reason": "Verification could not be read. Please try again."}

    if int(payload.get("uid", -1)) != int(user_id):
        return {"ok": False, "reason": "That verification belongs to a different account."}
    if int(payload.get("exp", 0)) < int(time.time()):
        return {"ok": False, "reason": "Your verification expired. Please verify again."}
    if academic_email and payload.get("email", "") != (academic_email or "").strip().lower():
        return {"ok": False, "reason": "That verification is for a different email address."}
    return {"ok": True, "reason": "verified", "payload": payload}


def status_for(user: Optional[dict]) -> dict:
    """The academic verification state the UI renders. `mailbox_proof_required`
    tells the UI whether mailbox confirmation is expected (it is, once SMTP is
    configured) so it can stop claiming the address was proven."""
    if not user:
        return {"required": True, "status": "none", "verified_email": None,
                "verified_at": None, "mailbox_proof_required": False}

    status = user.get("academic_status") or "none"
    return {
        "required": True,
        "status": status,
        "verified_email": user.get("academic_verified_email"),
        "verified_at": user.get("academic_verified_at"),
        "institute_name": user.get("institute_name"),
        "academician_type": user.get("academician_type"),
        # Mailbox proof needs SMTP; until then only the domain is checked.
        "mailbox_proof_required": bool(os.environ.get("ACADEMIC_REQUIRE_MAILBOX_PROOF", "").strip()),
    }


def is_verified(user: Optional[dict]) -> bool:
    """Server-side authority for the payment gate. Verified only when the status
    says so *and* the verified address is still the one on the profile."""
    if not user:
        return False
    if (user.get("academic_status") or "") != "verified":
        return False
    verified_email = (user.get("academic_verified_email") or "").strip().lower()
    profile_email = (user.get("academic_email") or "").strip().lower()
    # A profile without an address cannot contradict the verification, but one
    # that has since changed to a different address invalidates it.
    return bool(verified_email) and (not profile_email or verified_email == profile_email)