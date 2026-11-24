import { NextRequest, NextResponse } from 'next/server';
import { getDecisionIntel } from '@/lib/search-engine';

export const dynamic = 'force-dynamic';

/**
 * The verdict bundle for a finished search.
 *
 * Mirrors `GET /api/search/{id}/decision-intel` in python-api/app.py. This
 * route was simply missing, so on the deployed app the panel's fetch hit the
 * 404 page and the IP, prior-art, MoA and toxicity blocks could never appear.
 */
export async function GET(
  _request: NextRequest,
  { params }: { params: { id: string } }
) {
  const intel = await getDecisionIntel(params.id);
  if (!intel) {
    return NextResponse.json(
      { detail: 'Verdict not available for this search' },
      { status: 404 }
    );
  }
  return NextResponse.json(intel);
}
