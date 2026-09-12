import { NextRequest, NextResponse } from 'next/server';
import { hostedEngineUrl, fetchJson } from '@/lib/search-engine';

export const dynamic = 'force-dynamic';

/**
 * POST /api/exports
 * Submit a new search query to the hosted Python engine (production).
 * Returns the remote search_id so the frontend can poll /api/search/{id}.
 */
export async function POST(request: NextRequest) {
  const engineUrl = hostedEngineUrl();
  if (!engineUrl) {
    return NextResponse.json(
      { detail: 'No hosted search engine configured. Set SEARCH_ENGINE_URL.' },
      { status: 501 }
    );
  }

  try {
    const body = await request.json();
    const { query, query_type, hops } = body;

    if (!query || !query.trim()) {
      return NextResponse.json(
        { detail: 'query is required' },
        { status: 400 }
      );
    }

    const validatedHops =
      typeof hops === 'number' ? Math.max(1, Math.min(4, hops)) : 1;
    const validatedType =
      ['protein', 'ligand', 'auto'].includes(query_type)
        ? query_type
        : 'auto';

    const data = await fetchJson(`${engineUrl}/api/search`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        query: query.trim(),
        query_type: validatedType,
        hops: validatedHops,
      }),
      signal: AbortSignal.timeout(120_000),
    });

    return NextResponse.json({
      search_id: data.search_id,
      status: data.status,
      progress: data.progress,
      remote: true,
    });
  } catch (err: any) {
    return NextResponse.json(
      { detail: err.message || 'Hosted engine request failed' },
      { status: 502 }
    );
  }
}

// Keep GET for health-check friendly endpoints that probe /api/exports
export async function GET() {
  return NextResponse.json({ ok: true, service: 'exports' });
}
