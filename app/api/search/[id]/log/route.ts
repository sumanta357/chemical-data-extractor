import { NextRequest, NextResponse } from 'next/server';
import { getSearch } from '@/lib/search-engine';

export const dynamic = 'force-dynamic';

export async function GET(
  _request: NextRequest,
  { params }: { params: { id: string } }
) {
  const searchId = params.id;
  const state = await getSearch(searchId);

  if (!state) {
    return NextResponse.json(
      { detail: 'Search not found' },
      { status: 404 }
    );
  }

  const url = new URL(_request.url);
  const offset = parseInt(url.searchParams.get('offset') || '0', 10);

  return NextResponse.json({
    search_id: state.search_id,
    status: state.status,
    offset,
    total_lines: state.log.length,
    new_lines: state.log.slice(offset),
  });
}
