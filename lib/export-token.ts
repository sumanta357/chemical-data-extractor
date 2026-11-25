import crypto from 'crypto';

/**
 * Signed, expiring URLs for single export files.
 *
 * A browser download carries the session cookie. Cytoscape's "Network from
 * URL" does not: it fetches the URL with no cookie at all. So the graph has to
 * be reachable through a capability URL — an HMAC over (search_id, filename,
 * expiry) that the owner mints and that anyone holding the URL can use until
 * it expires.
 *
 * The FastAPI engine implements the same scheme in python-api/app.py. Both
 * deployments accept the same token shape; whichever host serves the download
 * verifies it with its own EXPORT_SIGNING_SECRET.
 */

export const EXPORT_LINK_TTL_SECONDS = Number(
  process.env.EXPORT_LINK_TTL_SECONDS ?? 604800
);

let secret = (process.env.EXPORT_SIGNING_SECRET ?? '').trim();
if (!secret) {
  // Local development still works, but links die with the process. Say so
  // rather than silently minting links that will mysteriously stop working.
  secret = crypto.randomBytes(32).toString('base64url');
  console.warn(
    '[export-link] EXPORT_SIGNING_SECRET is not set; signed export links expire on restart.'
  );
}

export function signExportToken(
  searchId: string,
  filename: string,
  expiresAt: number
): string {
  return crypto
    .createHmac('sha256', secret)
    .update(`${searchId}:${filename}:${expiresAt}`)
    .digest('hex');
}

export function verifyExportToken(
  searchId: string,
  filename: string,
  expiresAt: number,
  signature: string
): boolean {
  if (!Number.isFinite(expiresAt) || expiresAt * 1000 < Date.now()) return false;
  const expected = Buffer.from(signExportToken(searchId, filename, expiresAt), 'utf8');
  const supplied = Buffer.from(signature ?? '', 'utf8');
  if (expected.length !== supplied.length) return false;
  return crypto.timingSafeEqual(expected, supplied);
}

export interface ExportLink {
  file: string;
  path: string;
  url: string;
  expires_at: number;
  expires_in: number;
}

export function buildExportLink(
  origin: string,
  searchId: string,
  filename: string
): ExportLink {
  const expiresAt = Math.floor(Date.now() / 1000) + EXPORT_LINK_TTL_SECONDS;
  const signature = signExportToken(searchId, filename, expiresAt);
  const path =
    `/api/exports/${encodeURIComponent(filename)}` +
    `?search_id=${encodeURIComponent(searchId)}` +
    `&expires=${expiresAt}&signature=${signature}`;
  const base = origin.replace(/\/+$/, '');
  return {
    file: filename,
    path,
    url: `${base}${path}`,
    expires_at: expiresAt,
    expires_in: EXPORT_LINK_TTL_SECONDS,
  };
}
