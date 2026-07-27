import { useState, type FormEvent } from 'react';

interface Props {
  onSearch: (query: string, queryType: string, hops: number) => void;
  isRunning: boolean;
}

export default function SearchForm({ onSearch, isRunning }: Props) {
  const [query, setQuery] = useState('');
  const [queryType, setQueryType] = useState('auto');
  const [hops, setHops] = useState(1);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!query.trim() || isRunning) return;
    onSearch(query.trim(), queryType, hops);
  };

  return (
    <form onSubmit={handleSubmit} className="bg-gray-900 border border-gray-800 rounded-xl p-5 space-y-4 glow-border">
      <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wider">New Search</h2>

      {/* Query input */}
      <div>
        <label className="block text-xs text-gray-500 mb-1.5">Query</label>
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder='e.g. "tubulin", "Aspirin", "EGFR"'
          className="w-full bg-gray-950 border border-gray-700 rounded-lg px-3 py-2.5 text-sm text-white placeholder:text-gray-600 focus:outline-none focus:border-cyan-600 focus:ring-1 focus:ring-cyan-600/50 transition-colors"
          disabled={isRunning}
        />
      </div>

      {/* Query Type */}
      <div>
        <label className="block text-xs text-gray-500 mb-1.5">Query Type</label>
        <div className="flex gap-2">
          {(['auto', 'protein', 'ligand'] as const).map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setQueryType(t)}
              disabled={isRunning}
              className={`flex-1 px-3 py-2 rounded-lg text-xs font-medium transition-all ${
                queryType === t
                  ? 'bg-cyan-600/20 text-cyan-400 border border-cyan-700'
                  : 'bg-gray-800 text-gray-400 border border-gray-700 hover:border-gray-600'
              }`}
            >
              {t === 'auto' ? '🔄 Auto' : t === 'protein' ? '🧬 Protein' : '💊 Ligand'}
            </button>
          ))}
        </div>
      </div>

      {/* Hops */}
      <div>
        <label className="block text-xs text-gray-500 mb-1.5">Expansion Depth (Hops)</label>
        <div className="flex gap-2">
          {[1, 2, 3, 4].map((h) => (
            <button
              key={h}
              type="button"
              onClick={() => setHops(h)}
              disabled={isRunning}
              className={`flex-1 px-3 py-2 rounded-lg text-xs font-medium transition-all ${
                hops === h
                  ? 'bg-cyan-600/20 text-cyan-400 border border-cyan-700'
                  : 'bg-gray-800 text-gray-400 border border-gray-700 hover:border-gray-600'
              }`}
            >
              {h}-Hop
            </button>
          ))}
        </div>
      </div>

      {/* Submit */}
      <button
        type="submit"
        disabled={!query.trim() || isRunning}
        className="w-full py-2.5 rounded-lg font-semibold text-sm transition-all bg-cyan-600 hover:bg-cyan-500 text-white disabled:opacity-40 disabled:cursor-not-allowed disabled:hover:bg-cyan-600"
      >
        {isRunning ? (
          <span className="flex items-center justify-center gap-2">
            <svg className="animate-spin h-4 w-4" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            Running...
          </span>
        ) : (
          '🚀 Run Search'
        )}
      </button>
    </form>
  );
}
