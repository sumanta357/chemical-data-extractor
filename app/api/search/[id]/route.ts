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

  return NextResponse.json({
    search_id: state.search_id,
    query: state.query,
    // Without the depth a search reopened from History rendered "-hop
    // expansion": the response carried everything except the one number the
    // header prints.
    hops: state.hops,
    query_type: state.query_type,
    status: state.status,
    progress: state.progress,
    step: state.step ?? null,
    step_total: state.step_total ?? null,
    step_label: state.step_label ?? null,
    log: state.log,
    export_files: state.export_files,
    created_at: state.created_at,
    elapsed_seconds: state.elapsed_seconds,
    error: state.error,
  });
}
