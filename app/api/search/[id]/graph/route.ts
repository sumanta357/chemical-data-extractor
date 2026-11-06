import { NextRequest, NextResponse } from 'next/server';
import { getSearchGraph } from '@/lib/search-engine';

export const dynamic = 'force-dynamic';

/**
 * The graph behind a search, read from its own nodes.csv and edges.csv.
 *
 * Mirrors `GET /api/search/{id}/graph` in python-api/app.py, which serves the
 * same response in the Docker image. 404 means "no graph yet" — a running
 * search has no CSVs to read.
 */
export async function GET(
  _request: NextRequest,
  { params }: { params: { id: string } }
) {
  const graph = await getSearchGraph(params.id);
  if (!graph) {
    return NextResponse.json(
      { detail: 'Graph data not available yet' },
      { status: 404 }
    );
  }
  return NextResponse.json(graph);
}
