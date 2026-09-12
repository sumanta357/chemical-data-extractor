'use client';

import { useEffect, useState } from 'react';
import type { SearchSummary } from '@/lib/types';

interface Props {
  onViewSearch: (searchId: string) => void;
}

const STATUS_LABEL: Record<string, string> = {
  queued: 'queued',
  running: 'running',
  completed: 'completed',
  failed: 'failed',
};

const STATUS_COL: Record<string, string> = {
  queued: 'text-[#f0c63a]',
  running: 'text-[#5fb2c9]',
  completed: 'text-[#3fb950]',
  failed: 'text-[#f87171]',
};

export default function HistoryPanel({ onViewSearch }: Props) {
  const [searches, setSearches] = useState<SearchSummary[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch('/api/searches?limit=20')
      .then((res) => res.json())
      .then((data) => setSearches(data))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <span className="spinner-square" />
      </div>
    );
  }

  if (searches.length === 0) {
    return (
      <div className="card text-center py-14">
        <p className="text-[10px] font-semibold text-[rgb(95 178 201)] uppercase tracking-wider">
          No searches yet
        </p>
        <p className="text-sm text-[rgb(90 96 120)] mt-2">
          Run a search to see it appear here.
        </p>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-[10px] font-semibold text-[rgb(95 178 201)] uppercase tracking-wider">
          Search History
        </h2>
        <span className="text-[10px] text-[rgb(90 96 120)] font-mono">
          {searches.length} session{searches.length === 1 ? '' : 's'}
        </span>
      </div>

      <div className="space-y-2">
        {searches.map((s) => {
          const col = STATUS_COL[s.status] ?? STATUS_COL.queued;
          return (
            <button
              key={s.search_id}
              onClick={() => onViewSearch(s.search_id)}
              className="card w-full text-left px-4 py-3 hover:border-[rgb(63 185 80)] hover:shadow-sm transition-all group"
            >
              <div className="flex items-center justify-between min-w-0">
                <div className="flex items-center gap-2.5 min-w-0">
                  {/* Status tag */}
                  <span
                    className={`shrink-0 text-[9px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded border ${col} border-current`}
                  >
                    {STATUS_LABEL[s.status] ?? s.status}
                  </span>

                  <div className="min-w-0">
                    <p className="text-sm font-medium text-[rgb(200 208 220)] group-hover:text-[rgb(95 178 201)] truncate transition-colors">
                      {s.query}
                    </p>
                    <p className="text-[10px] text-[rgb(90 96 120)] mt-0.5 font-mono">
                      {s.search_id}
                      {' · '}
                      {s.file_count} file{s.file_count === 1 ? '' : 's'}
                      {s.elapsed_seconds != null ? ` · ${s.elapsed_seconds.toFixed(1)}s` : ''}
                    </p>
                  </div>
                </div>

                <span className="shrink-0 text-[10px] text-[rgb(90 96 120)] ml-3 font-mono">
                  {new Date(s.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                </span>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
