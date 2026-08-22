import { NextRequest, NextResponse } from 'next/server';
import { listSearches } from '@/lib/search-engine';

export const dynamic = 'force-dynamic';

export async function GET(request: NextRequest) {
  const url = new URL(request.url);
  const limit = parseInt(url.searchParams.get('limit') || '20', 10);

  const searches = listSearches(Math.min(limit, 100));

  return NextResponse.json(
    searches.map((s) => ({
      search_id: s.search_id,
      query: s.query,
      status: s.status,
      progress: s.progress,
      created_at: s.created_at,
      elapsed_seconds: s.elapsed_seconds,
      file_count: s.export_files.length,
    }))
  );
}
