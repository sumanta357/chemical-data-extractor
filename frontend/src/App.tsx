import { useState, useEffect, useRef, useCallback } from 'react';
import SearchForm from './components/SearchForm';
import ProgressView from './components/ProgressView';
import ResultsView from './components/ResultsView';
import HistoryPanel from './components/HistoryPanel';
import { startSearch, getSearchStatus, getSearchLog } from './api';
import type { SearchStatus } from './types';

export default function App() {
  const [search, setSearch] = useState<SearchStatus | null>(null);
  const [logLines, setLogLines] = useState<string[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [activeTab, setActiveTab] = useState<'search' | 'history'>('search');
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const handleSearch = useCallback(async (query: string, queryType: string, hops: number) => {
    setIsRunning(true);
    setLogLines([]);
    setSearch(null);

    try {
      const { search_id } = await startSearch(query, queryType, hops);
      const initial = await getSearchStatus(search_id);
      setSearch(initial);
      setLogLines(initial.log || []);
      startPolling(search_id);
    } catch (err: any) {
      setSearch({
        search_id: 'error',
        query,
        status: 'failed',
        progress: 'Failed to start',
        log: [err.message],
        export_files: [],
        created_at: new Date().toISOString(),
        elapsed_seconds: null,
        error: err.message,
      });
      setIsRunning(false);
    }
  }, []);

  const startPolling = useCallback((searchId: string) => {
    let offset = 0;

    pollingRef.current = setInterval(async () => {
      try {
        const status = await getSearchStatus(searchId);
        setSearch(status);
        setLogLines((status as SearchStatus).log || []);

        const logRes = await getSearchLog(searchId, offset);
        if (logRes.new_lines?.length > 0) {
          offset = logRes.total_lines;
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
    getSearchStatus(searchId).then((status) => {
      setSearch(status);
      setLogLines(status.log || []);
      setActiveTab('search');
    });
  }, []);

  return (
    <div className="min-h-screen flex flex-col">
      {/* Header */}
      <header className="border-b border-gray-800 bg-gray-900/80 backdrop-blur-sm sticky top-0 z-50">
        <div className="max-w-7xl mx-auto px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className="text-2xl">🔬</span>
            <div>
              <h1 className="text-lg font-bold tracking-tight text-white">SciGraph</h1>
              <p className="text-xs text-gray-500 -mt-0.5">Knowledge Graph Platform v3.1</p>
            </div>
          </div>
          <nav className="flex gap-1">
            <button
              onClick={() => setActiveTab('search')}
              className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                activeTab === 'search'
                  ? 'bg-cyan-600/20 text-cyan-400'
                  : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800'
              }`}
            >
              🔎 Search
            </button>
            <button
              onClick={() => setActiveTab('history')}
              className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-colors ${
                activeTab === 'history'
                  ? 'bg-cyan-600/20 text-cyan-400'
                  : 'text-gray-400 hover:text-gray-200 hover:bg-gray-800'
              }`}
            >
              📋 History
            </button>
          </nav>
        </div>
      </header>

      {/* Main */}
      <main className="flex-1 max-w-7xl mx-auto w-full px-4 py-6">
        {activeTab === 'search' && (
          <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
            {/* Left: Search form */}
            <div className="lg:col-span-2 space-y-6">
              <SearchForm onSearch={handleSearch} isRunning={isRunning} />

              {search?.status === 'completed' && search.export_files.length > 0 && (
                <ResultsView search={search} />
              )}
            </div>

            {/* Right: Progress / Log */}
            <div className="lg:col-span-3">
              {search ? (
                <ProgressView search={search} logLines={logLines} />
              ) : (
                <div className="h-full flex items-center justify-center text-gray-600 py-20">
                  <div className="text-center">
                    <div className="text-5xl mb-4">🔬</div>
                    <p className="text-lg">Enter a query to start searching</p>
                    <p className="text-sm mt-1">Search proteins, compounds, and pathways across 19 databases</p>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {activeTab === 'history' && (
          <HistoryPanel onViewSearch={handleViewSearch} />
        )}
      </main>

      {/* Footer */}
      <footer className="border-t border-gray-800 py-3 text-center text-xs text-gray-600">
        SciGraph v3.1 — 19 Database Connectors · Multi-Hop Expansion · Enterprise Knowledge Graph
      </footer>
    </div>
  );
}
