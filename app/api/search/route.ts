import { NextRequest, NextResponse } from 'next/server';
import { createSearch, getSearch } from '@/lib/search-engine';
import { ownerKeyForNewSearch, rememberOwner } from '@/lib/search-owner';

export const dynamic = 'force-dynamic';

export async function POST(request: NextRequest) {
  try {
    const body = await request.json();
    const { query, query_type, hops } = body;

    // Validate
    if (!query || !query.trim()) {
      return NextResponse.json(
        { detail: 'Query is required' },
        { status: 400 }
      );
    }

    const validatedHops = typeof hops === 'number' ? Math.max(1, Math.min(4, hops)) : 1;
    const validatedType = ['protein', 'ligand', 'auto'].includes(query_type)
      ? query_type
      : 'auto';

    // The search belongs to the browser that started it: exports accept either
    // a signed token or that owner, never a bare search id.
    const ownerKey = ownerKeyForNewSearch(request);
    const state = createSearch(query.trim(), validatedType, validatedHops, ownerKey);

    // Wait briefly for initial state
    await new Promise((resolve) => setTimeout(resolve, 500));

    const current = await getSearch(state.search_id);
    if (!current) {
      return NextResponse.json(
        { detail: 'Search state lost' },
        { status: 500 }
      );
    }
    return rememberOwner(
      NextResponse.json({
        search_id: current.search_id,
        query: current.query,
        status: current.status,
        progress: current.progress,
        log: current.log,
        export_files: current.export_files,
        created_at: current.created_at,
        elapsed_seconds: current.elapsed_seconds,
        error: current.error,
      }),
      ownerKey,
      request
    );
  } catch (err: any) {
    return NextResponse.json(
      { detail: err.message || 'Invalid request' },
      { status: 400 }
    );
  }
}
