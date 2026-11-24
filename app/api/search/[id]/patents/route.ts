import { NextRequest, NextResponse } from 'next/server';
import { getSearchPatents } from '@/lib/search-engine';

export const dynamic = 'force-dynamic';

/**
 * The patents a search surfaced, read from its own nodes.csv and edges.csv.
 *
 * Mirrors `GET /api/search/{id}/patents` in python-api/app.py, which serves the
 * same response in the Docker image. 404 means "no exports yet" — a running
 * search has no CSVs to read.
 */
export async function GET(
  _request: NextRequest,
  { params }: { params: { id: string } }
) {
  const patents = await getSearchPatents(params.id);
  if (!patents) {
    return NextResponse.json(
      { detail: 'Patent data not available yet' },
      { status: 404 }
    );
  }
  return NextResponse.json({ search_id: params.id, ...patents });
}
