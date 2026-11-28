import { NextRequest, NextResponse } from 'next/server';
import { getSearchExportLink, getSearchOwnership } from '@/lib/search-engine';
import { requestOwnsSearch } from '@/lib/search-owner';

export const dynamic = 'force-dynamic';

/**
 * The origin a browser (or Cytoscape importing from a URL) can reach.
 *
 * `request.url` inside a route handler is built from the server's bind
 * address, so minting a link from it produced `http://0.0.0.0:3001/...` behind
 * the preview proxy: a URL that works on the server and nowhere else. Mirrors
 * python-api/app.py's origin fallback — PUBLIC_BASE_URL when configured, then
 * the forwarded scheme/host, then the Host header.
 */
function publicOrigin(request: NextRequest, fallback: string): string {
  const configured = (process.env.PUBLIC_BASE_URL ?? '').trim().replace(/\/+$/, '');
  if (configured) {
    try {
      const url = new URL(configured);
      if (
        (url.protocol === 'http:' || url.protocol === 'https:') &&
        (url.pathname === '/' || url.pathname === '')
      ) {
        return configured;
      }
    } catch {
      // Malformed value: fall back to the request's own headers.
    }
  }

  const first = (value: string | null) => (value ?? '').split(',')[0].trim();
  const forwardedScheme = first(request.headers.get('x-forwarded-proto'));
  const scheme = forwardedScheme === 'http' || forwardedScheme === 'https'
    ? forwardedScheme
    : request.nextUrl.protocol === 'http:'
      ? 'http'
      : 'https';
  const host =
    first(request.headers.get('x-forwarded-host')) ||
    first(request.headers.get('host'));
  return host ? `${scheme}://${host}` : fallback;
}

/**
 * A signed, expiring URL for one export file.
 *
 * This is what lets a finished search open directly in Cytoscape: the owner
 * asks for a capability URL here, then pastes it into
 * File → Import → Network from URL. Mirrors
 * `GET /api/search/{id}/export-link` in python-api/app.py.
 *
 * Minting is owner-only. A hosted search is checked by the engine, which knows
 * the account (the caller's token is forwarded); a locally run search has no
 * account, so the browser that created it is the owner.
 */
export async function GET(
  request: NextRequest,
  { params }: { params: { id: string } }
) {
  const url = new URL(request.url);
  const filename = url.searchParams.get('file') || 'graph.graphml';

  const ownership = getSearchOwnership(params.id);
  if (!ownership) {
    return NextResponse.json({ detail: 'Search not found' }, { status: 404 });
  }
  if (!ownership.hosted && !requestOwnsSearch(request, ownership.ownerKey)) {
    return NextResponse.json({ detail: 'Search not found' }, { status: 404 });
  }

  const link = await getSearchExportLink(
    params.id,
    filename,
    publicOrigin(request, url.origin),
    request.headers.get('authorization')
  );

  if (!link) {
    return NextResponse.json(
      {
        detail: `No signed link for ${filename}. The file may not exist for this search yet.`,
      },
      { status: 404 }
    );
  }

  return NextResponse.json({ search_id: params.id, ...link });
}
