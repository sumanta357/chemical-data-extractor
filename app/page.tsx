'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import SearchForm from '@/components/SearchForm';
import ProgressView from '@/components/ProgressView';
import ResultsView from '@/components/ResultsView';
import HistoryPanel from '@/components/HistoryPanel';
import AIAssistant from '@/components/AIAssistant';
import type { SearchState } from '@/lib/types';

export default function HomePage() {
  const [search, setSearch] = useState<SearchState | null>(null);
  const [logLines, setLogLines] = useState<string[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [activeTab, setActiveTab] = useState<'search' | 'history'>('search');
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const handleSearch = useCallback(
    async (query: string, queryType: string, hops: number) => {
      setIsRunning(true);
      setLogLines([]);
      setSearch(null);

      try {
        const res = await fetch('/api/search', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query, query_type: queryType, hops }),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({ detail: res.statusText }));
          throw new Error(err.detail || 'Search failed to start');
        }
        const data = await res.json();
        setSearch(data);
        setLogLines(data.log || []);
        startPolling(data.search_id);
      } catch (err: any) {
        setSearch({
          search_id: 'error',
          query,
          query_type: queryType,
          hops,
          status: 'failed',
          progress: 'Failed to start',
          log: [err.message],
          export_files: [],
          export_dir: '',
          created_at: new Date().toISOString(),
          elapsed_seconds: null,
          error: err.message,
        });
        setIsRunning(false);
      }
    },
    []
  );

  const startPolling = useCallback((searchId: string) => {
    try { localStorage.setItem('scigraph_active_search', searchId); } catch {}

    pollingRef.current = setInterval(async () => {
      try {
        const res = await fetch(`/api/search/${searchId}`);

        if (!res.ok) {
          if (pollingRef.current) clearInterval(pollingRef.current);
          pollingRef.current = null;
          setIsRunning(false);
          return;
        }

        const data = await res.json();
        setSearch(data);
        setLogLines(data.log || []);

        if (data.status === 'completed' || data.status === 'failed') {
          if (pollingRef.current) clearInterval(pollingRef.current);
          pollingRef.current = null;
          setIsRunning(false);
          try { localStorage.removeItem('scigraph_active_search'); } catch {}
        }
      } catch {
        if (pollingRef.current) clearInterval(pollingRef.current);
        pollingRef.current = null;
        setIsRunning(false);
      }
    }, 1500);
  }, []);

  useEffect(() => () => {
    if (pollingRef.current) clearInterval(pollingRef.current);
  }, []);

  // Session restore: if a search was active before page refresh, resume it
  useEffect(() => {
    try {
      const savedId = localStorage.getItem('scigraph_active_search');
      if (savedId) {
        fetch(`/api/search/${savedId}`)
          .then((res) => res.json())
          .then((data) => {
            if (data && (data.status === 'running' || data.status === 'queued')) {
              setSearch(data);
              setLogLines(data.log || []);
              setIsRunning(true);
              startPolling(savedId);
            } else if (data && data.status === 'completed' && data.export_files?.length > 0) {
              setSearch(data);
              setLogLines(data.log || []);
              try { localStorage.removeItem('scigraph_active_search'); } catch {}
            } else {
              try { localStorage.removeItem('scigraph_active_search'); } catch {}
            }
          })
          .catch(() => {
            try { localStorage.removeItem('scigraph_active_search'); } catch {}
          });
      }
    } catch {}
  }, [startPolling]);

  const handleViewSearch = useCallback((searchId: string) => {
    fetch(`/api/search/${searchId}`)
      .then((res) => res.json())
      .then((data) => {
        setSearch(data);
        setLogLines(data.log || []);
        setActiveTab('search');
      })
      .catch(() => {});
  }, []);

  return (
    <div className="min-h-screen flex flex-col relative bg-[rgb(15 17 23)] text-[rgb(225 229 234)]">
      {/* Soft dot grid */}
      <div className="grid-overlay" />

      {/* Header */}
      <header className="sticky top-0 z-50 border-b border-[rgb(42 45 53)] bg-[rgb(15 17 23)]/90">
        <div className="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            {/* Minimal brand mark — small square emblem, no emoji, no glow */}
            <span
              className="shrink-0 w-7 h-7 rounded flex items-center justify-center text-[10px] font-semibold tracking-widest"
              style={{
                background: 'rgb(63 185 80)',
                color: '#0a0c12',
              }}
            >
              CDE
            </span>
            <div>
              <h1 className="text-sm font-semibold tracking-tight text-[rgb(225 229 234)]">
                Chemical Data Extractor
              </h1>
              <p className="text-[10px] text-[rgb(90 96 120)] -mt-0.5 font-medium">
                Knowledge Graph Platform v3.2
              </p>
            </div>
          </div>

          <nav className="flex gap-1 bg-[rgb(28 30 38)] rounded-lg p-1 border border-[rgb(42 45 53)]">
            {(['search', 'history'] as const).map((t) => (
              <button
                key={t}
                onClick={() => setActiveTab(t)}
                className={`px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
                  activeTab === t
                    ? 'bg-[rgb(42 45 53)] text-[rgb(200 208 220)] shadow-sm'
                    : 'text-[rgb(90 96 120)] hover:text-[rgb(200 208 220)]'
                }`}
              >
                {t === 'search' ? 'Search' : 'History'}
              </button>
            ))}
          </nav>
        </div>
      </header>

      {/* Top strip — compact, no oversized hero */}
      <div className="relative z-10 border-b border-[rgb(42 45 53)] bg-[rgb(12 14 20)]">
        <div className="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between text-xs">
          <div>
            <span className="font-semibold text-[rgb(200 208 220)]">Multi-hop discovery engine</span>
            <span className="text-[rgb(90 96 120)] ml-1">·</span>
            <span className="text-[rgb(90 96 120)]">
              Compounds, bioactivities, protein targets, 3D structures, pathways
            </span>
          </div>
          <div className="flex items-center gap-3 font-mono text-[10px] text-[rgb(90 96 120)]">
            <span className="flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-[rgb(63 185 80)]" />
              19+ databases
            </span>
            <span className="flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-[rgb(95 178 201)]" />
              4-hop depth
            </span>
            <span className="flex items-center gap-1">
              <span className="w-1.5 h-1.5 rounded-full bg-[rgb(95 178 201)]" />
              7+ export formats
            </span>
          </div>
        </div>
      </div>

      {/* Main */}
      <main className="flex-1 max-w-7xl mx-auto w-full px-4 py-6 relative z-10">
        {activeTab === 'search' && (
          <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
            {/* Left column */}
            <div className="lg:col-span-2 space-y-5">
              <SearchForm onSearch={handleSearch} isRunning={isRunning} />

              {/* AI analyst — distinct indigo panel below the form */}
              <AIAssistant
                searchId={search && search.search_id !== 'error' ? search.search_id : null}
                query={search?.query ?? null}
              />

              {search?.status === 'completed' &&
                search.export_files.length > 0 && (
                  <ResultsView search={search} />
                )}
            </div>

            {/* Right column */}
            <div className="lg:col-span-3">
              {search ? (
                <ProgressView search={search} logLines={logLines} />
              ) : (
                <div className="card flex flex-col items-center justify-center text-center py-14">
                  <p className="text-[10px] font-semibold text-[rgb(95 178 201)] uppercase tracking-wider mb-2">
                    Ready
                  </p>
                  <p className="text-sm font-medium text-[rgb(200 208 220)] mb-1">
                    Enter a query to start a search
                  </p>
                  <p className="text-xs text-[rgb(90 96 120)] max-w-xs">
                    Compounds, bioactivities, protein targets, 3D structures,
                    and pathways from 19+ scientific databases.
                  </p>
                </div>
              )}
            </div>
          </div>
        )}

        {activeTab === 'history' && (
          <HistoryPanel onViewSearch={handleViewSearch} />
        )}
      </main>

      {/* Footer — compact, honest */}
      <footer className="border-t border-[rgb(42 45 53)] py-4 text-center text-[10px] text-[rgb(90 96 120)] relative z-10 bg-[rgb(12 14 20)]">
        <div className="max-w-7xl mx-auto px-4 flex flex-col sm:flex-row items-center justify-between gap-1">
          <span>Chemical Data Extractor · v3.2</span>
          <span>19 database connectors · multi-hop graph traversal · enrichment pipeline</span>
          <span>Sumanta</span>
        </div>
      </footer>
    </div>
  );
}
