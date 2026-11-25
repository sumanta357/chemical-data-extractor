import { NextRequest, NextResponse } from 'next/server';
import { getSearchExportLink, getSearchOwnership } from '@/lib/search-engine';
import { requestOwnsSearch } from '@/lib/search-owner';

export const dynamic = 'force-dynamic';

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
    url.origin,
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
