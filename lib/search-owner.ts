import crypto from 'crypto';
import type { NextRequest, NextResponse } from 'next/server';

/**
 * Who may download a search's exports.
 *
 * `/api/exports/<file>` accepts a signed token or proof that the caller owns the
 * search, exactly as the Python engine does. This app keeps no server-side
 * session — the Supabase token lives in the browser — so the owner here is the
 * browser that ran the search, remembered in an httpOnly cookie. A hosted
 * engine keeps its own account-based ownership; there the caller's bearer token
 * is forwarded and the engine decides.
 *
 * The key is a random server-side identifier, never anything derived from the
 * account, so it cannot be guessed and reveals nothing about the user.
 */

export const OWNER_COOKIE = 'scigraph_owner';
const OWNER_COOKIE_MAX_AGE_SECONDS = 30 * 24 * 60 * 60;
const OWNER_KEY_RE = /^[a-f0-9]{32,64}$/;

/** The owner key this browser already carries, or null. */
export function ownerKeyFromCookie(request: NextRequest): string | null {
  const value = request.cookies.get(OWNER_COOKIE)?.value?.trim() ?? '';
  return OWNER_KEY_RE.test(value) ? value : null;
}

export function newOwnerKey(): string {
  return crypto.randomBytes(24).toString('hex');
}

/** The owner key to record for a search this request creates. */
export function ownerKeyForNewSearch(request: NextRequest): string {
  return ownerKeyFromCookie(request) ?? newOwnerKey();
}

/**
 * True when this request may act for a search with `ownerKey`.
 *
 * A search created before owners were recorded carries `null` and stays
 * readable, matching the engine's guest-tier behaviour.
 */
export function requestOwnsSearch(
  request: NextRequest,
  ownerKey: string | null | undefined
): boolean {
  if (!ownerKey) return true;
  const caller = ownerKeyFromCookie(request);
  return caller !== null && caller === ownerKey;
}

/** Set the owner cookie when the request arrived without one. */
export function rememberOwner(
  response: NextResponse,
  ownerKey: string,
  request: NextRequest
): NextResponse {
  if (request.cookies.get(OWNER_COOKIE)?.value === ownerKey) return response;
  response.cookies.set(OWNER_COOKIE, ownerKey, {
    httpOnly: true,
    sameSite: 'lax',
    secure: process.env.NODE_ENV === 'production',
    path: '/',
    maxAge: OWNER_COOKIE_MAX_AGE_SECONDS,
  });
  return response;
}
