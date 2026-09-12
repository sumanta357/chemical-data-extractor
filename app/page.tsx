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
    let offset = 0;

    pollingRef.current = setInterval(async () => {
      try {
        const [statusRes, logRes] = await Promise.all([
          fetch(`/api/search/${searchId}`),
          fetch(`/api/search/${searchId}/log?offset=${offset}`),
        ]);

        if (!statusRes.ok) {
          if (pollingRef.current) clearInterval(pollingRef.current);
          pollingRef.current = null;
          setIsRunning(false);
          return;
        }

        const status = await statusRes.json();
        setSearch(status);
        setLogLines(status.log || []);

        if (logRes.ok) {
          const logData = await logRes.json();
          if (logData.new_lines?.length > 0) {
            offset = logData.total_lines;
          }
        }

        if (status.status === 'completed' || status.status === 'failed') {
          if (pollingRef.current) clearInterval(pollingRef.current);
          pollingRef.current = null;
          setIsRunning(false);
        }
      } catch {
        if (pollingRef.current) clearInterval(pollingRef.current);
        pollingRef.current = null;
        setIsRunning(false);
      }
    }, 1000);
  }, []);

  useEffect(() => {
    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current);
    };
  }, []);

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
    <div className="min-h-screen flex flex-col relative">
      {/* Animated particles */}
      <div className="particle-container" id="particles" />

      {/* Grid overlay */}
      <div className="grid-overlay" />

      {/* Header */}
      <header className="border-b border-gray-800/50 bg-[rgb(3,7,18)]/85 backdrop-blur-xl sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-2xl animate-pulse-glow">🔬</span>
            <div>
              <h1 className="text-lg font-bold tracking-tight text-white">
                Chemical Data Extractor
              </h1>
              <p className="text-xs text-cyan-400/70 -mt-0.5 font-medium">
                Knowledge Graph Platform v3.2
              </p>
            </div>
          </div>
          <nav className="flex gap-1">
            <button
              onClick={() => setActiveTab('search')}
              className={`flex items-center gap-2 px-4 py-1.5 rounded-lg text-sm font-medium transition-all ${
                activeTab === 'search'
                  ? 'bg-cyan-600/20 text-cyan-400 border border-cyan-700/50 shadow-lg shadow-cyan-500/10'
                  : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800/50 border border-transparent'
              }`}
            >
              <svg viewBox="0 0 24 24" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2}>
                <circle cx="11" cy="11" r="7" strokeLinecap="round" />
                <path d="M21 21l-4.35-4.35" strokeLinecap="round" />
              </svg>
              Search
            </button>
            <button
              onClick={() => setActiveTab('history')}
              className={`flex items-center gap-2 px-4 py-1.5 rounded-lg text-sm font-medium transition-all ${
                activeTab === 'history'
                  ? 'bg-cyan-600/20 text-cyan-400 border border-cyan-700/50 shadow-lg shadow-cyan-500/10'
                  : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800/50 border border-transparent'
              }`}
            >
              <svg viewBox="0 0 24 24" className="w-4 h-4" fill="none" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2" />
              </svg>
              History
            </button>
          </nav>
        </div>
      </header>

      {/* Hero */}
      <div className="relative z-10 text-center py-12 px-4 overflow-hidden">
        {/* Hero glow */}
        <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[800px] h-[400px] bg-gradient-radial from-cyan-500/5 via-purple-500/3 to-transparent pointer-events-none" />

        {/* Floating molecules — ambient, decorative only */}
        <div className="absolute inset-0 pointer-events-none overflow-hidden">
          {['⚗️','🧬','💊','🔬','⚛️','🧪'].map((emoji, i) => (
            <span
              key={i}
              aria-hidden="true"
              className="absolute text-2xl"
              style={{
                left: `${15 + i * 14}%`,
                top: `${20 + (i % 3) * 25}%`,
                opacity: 0.12,
                animation: `molecule-drift ${20 + i * 2}s ease-in-out infinite`,
                animationDelay: `${-i * 3}s`,
              }}
            >
              {emoji}
            </span>
          ))}
        </div>

        <h2 className="text-4xl md:text-5xl font-extrabold tracking-tight mb-4 relative">
          <span className="gradient-text">Chemical Data Extractor</span>
        </h2>
        <p className="text-gray-400 text-lg max-w-xl mx-auto leading-relaxed relative">
          Multi-hop automated discovery engine. Extract chemical compounds,
          bioactivities, protein targets, and biological pathways from 19+ scientific databases.
        </p>

        {/* Stats */}
        <div className="flex gap-6 justify-center flex-wrap mt-8 relative">
          {[
            { num: '19+', label: 'Databases' },
            { num: '4', label: 'Hop Depth' },
            { num: '7+', label: 'Export Formats' },
            { num: '15', label: 'Export Files' },
          ].map((stat, i) => (
            <div
              key={stat.label}
              className="glass-card px-6 py-4 text-center min-w-[100px] fade-in-up"
              style={{ animationDelay: `${i * 0.1}s` }}
            >
              <div className="text-2xl font-extrabold gradient-text tabular-nums">{stat.num}</div>
              <div className="text-[10px] text-gray-500 uppercase tracking-[0.08em] font-semibold mt-1">
                {stat.label}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Main */}
      <main className="flex-1 max-w-7xl mx-auto w-full px-4 py-6 relative z-10">
        {activeTab === 'search' && (
          <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
            {/* Left: Search form */}
            <div className="lg:col-span-2 space-y-6">
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

            {/* Right: Progress / Log */}
            <div className="lg:col-span-3">
              {search ? (
                <ProgressView search={search} logLines={logLines} />
              ) : (
                <>
                  <div className="glass-card flex items-center justify-center text-gray-600 py-16">
                    <div className="text-center">
                      <div className="text-5xl mb-4 animate-float">🔬</div>
                      <p className="text-lg font-semibold text-gray-300">
                        Enter a query to start searching
                      </p>
                      <p className="text-sm mt-2 text-gray-500 leading-relaxed max-w-sm mx-auto">
                        Extract chemical compounds, bioactivities, protein targets,
                        3D structures, and biological pathways from 19+ scientific databases.
                      </p>
                    </div>
                  </div>
                  <div className="grid grid-cols-3 gap-4 mt-6">
                    {[
                      { icon: '🧬', title: 'Proteins', desc: 'UniProt, PDB, AlphaFold' },
                      { icon: '💊', title: 'Compounds', desc: 'PubChem, ChEMBL, ChEBI' },
                      { icon: '🔗', title: 'Pathways', desc: 'KEGG, Reactome, GO' },
                    ].map((card, i) => (
                      <div
                        key={card.title}
                        className="glass-card p-4 text-center fade-in-up"
                        style={{ animationDelay: `${0.1 + i * 0.1}s` }}
                      >
                        <div className="text-2xl mb-2">{card.icon}</div>
                        <div className="text-sm font-semibold text-gray-300">{card.title}</div>
                        <div className="text-xs text-gray-500 mt-1">{card.desc}</div>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </div>
          </div>
        )}

        {activeTab === 'history' && (
          <HistoryPanel onViewSearch={handleViewSearch} />
        )}
      </main>

      {/* Footer */}
      <footer className="border-t border-gray-800/50 py-5 text-center text-xs text-gray-600 relative z-10 bg-[rgb(3,7,18)]/50 backdrop-blur-sm">
        <div className="max-w-7xl mx-auto px-4">
          <p className="font-semibold text-gray-400 text-sm">Chemical Data Extractor</p>
          <p className="mt-1">19 database connectors · Multi-hop graph traversal · Enrichment pipeline</p>
          <p className="mt-2 text-gray-500">
            Developed with ❤️ by <span className="text-cyan-400 font-semibold">Sumanta</span>
          </p>
        </div>
      </footer>
    </div>
  );
}
