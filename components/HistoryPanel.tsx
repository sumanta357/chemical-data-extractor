'use client';

import { useEffect, useState } from 'react';
import type { SearchState, SearchSummary } from '@/lib/types';

interface Props {
  onViewSearch: (searchId: string) => void;
}

const statusStyles: Record<string, { color: string; icon: string }> = {
  completed: { color: 'text-green-400', icon: '✅' },
  running: { color: 'text-cyan-400', icon: '🔄' },
  failed: { color: 'text-red-400', icon: '❌' },
  queued: { color: 'text-yellow-400', icon: '⏳' },
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
        <div className="animate-spin h-6 w-6 border-2 border-cyan-500 border-t-transparent rounded-full" />
      </div>
    );
  }

  if (searches.length === 0) {
    return (
      <div className="text-center py-20 text-gray-600">
        <div className="text-4xl mb-3">📋</div>
        <p className="text-lg">No searches yet</p>
        <p className="text-sm mt-1">Run a search to see it here</p>
      </div>
    );
  }

  return (
    <div>
      <h2 className="text-lg font-semibold text-gray-200 mb-4">Search History</h2>
      <div className="space-y-2">
        {searches.map((s) => {
          const st = statusStyles[s.status] || statusStyles.queued;
          return (
            <button
              key={s.search_id}
              onClick={() => onViewSearch(s.search_id)}
              className="w-full text-left bg-gray-900 border border-gray-800 rounded-lg p-4 hover:border-gray-700 transition-colors group"
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-3 min-w-0">
                  <span>{st.icon}</span>
                  <div className="min-w-0">
                    <p className={`font-medium text-sm truncate ${st.color}`}>
                      {s.query}
                    </p>
                    <p className="text-xs text-gray-600 mt-0.5">
                      {s.search_id} · {s.file_count} files
                      {s.elapsed_seconds
                        ? ` · ${s.elapsed_seconds.toFixed(1)}s`
                        : ''}
                    </p>
                  </div>
                </div>
                <span className="text-xs text-gray-600 shrink-0 ml-4">
                  {new Date(s.created_at).toLocaleTimeString()}
                </span>
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}
