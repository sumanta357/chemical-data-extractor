'use client';

import { useState, type FormEvent } from 'react';

interface Props {
  onSearch: (query: string, queryType: string, hops: number) => void;
  isRunning: boolean;
}

export default function SearchForm({ onSearch, isRunning }: Props) {
  const [query, setQuery] = useState('');
  const [queryType, setQueryType] = useState<'auto' | 'protein' | 'ligand'>('auto');
  const [hops, setHops] = useState(1);

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!query.trim() || isRunning) return;
    onSearch(query.trim(), queryType, hops);
  };

  return (
    <form
      onSubmit={handleSubmit}
      className="bg-[#0f1117] border border-[#2a2d35] rounded-xl p-5 space-y-4"
    >
      <div>
        <h2 className="text-xs font-semibold text-[#8892a0] uppercase tracking-wider">
          New Search
        </h2>
        <div className="h-px bg-[#2a2d35] mt-3 mb-4" />
      </div>

      {/* Instructions */}
      <div className="border border-[#2a2d35] rounded-lg p-3 space-y-2">
        <h3 className="text-[10px] font-semibold text-[#5fb2c9] uppercase tracking-wider">
          How it works
        </h3>
        <ol className="text-[11px] text-[#5a6478] space-y-1.5">
          <li className="flex gap-2.5">
            <span className="text-[#5fb2c9] font-bold shrink-0">1.</span>
            <span>Enter a <span className="text-[#c8d0dc] font-medium">protein</span> (e.g. "tubulin", "EGFR") or <span className="text-[#c8d0dc] font-medium">compound</span> (e.g. "Aspirin", "Ibuprofen")</span>
          </li>
          <li className="flex gap-2.5">
            <span className="text-[#5fb2c9] font-bold shrink-0">2.</span>
            <span>Choose <span className="text-[#c8d0dc] font-medium">Auto</span> to detect, or pick <span className="text-[#c8d0dc] font-medium">Protein/Ligand</span> manually</span>
          </li>
          <li className="flex gap-2.5">
            <span className="text-[#5fb2c9] font-bold shrink-0">3.</span>
            <span>Select <span className="text-[#c8d0dc] font-medium">hops</span> — how many connection steps to explore</span>
          </li>
          <li className="flex gap-2.5">
            <span className="text-[#5fb2c9] font-bold shrink-0">4.</span>
            <span>Click <span className="text-[#c8d0dc] font-medium">Run Search</span> and watch the extraction in real-time</span>
          </li>
        </ol>
      </div>

      {/* What are Hops? */}
      <div className="border border-[#2a2d35] rounded-lg p-3">
        <h3 className="text-[10px] font-semibold text-[#5fb2c9] uppercase tracking-wider mb-2">
          What are Hops?
        </h3>
        <div className="text-[11px] text-[#5a6478] space-y-1.5">
          <div className="flex items-start gap-2">
            <span className="text-[#3fb950] font-mono font-bold shrink-0">1-hop</span>
            <span>Direct connections only (e.g., Aspirin → COX-1 enzyme)</span>
          </div>
          <div className="flex items-start gap-2">
            <span className="text-[#5fb2c9] font-mono font-bold shrink-0">2-hop</span>
            <span>Follow one more step (e.g., Aspirin → COX-1 → Prostaglandin pathway)</span>
          </div>
          <div className="flex items-start gap-2">
            <span className="text-[#a371f7] font-mono font-bold shrink-0">3-hop</span>
            <span>Deeper network (e.g., Aspirin → COX-1 → Pathway → Related diseases)</span>
          </div>
          <div className="flex items-start gap-2">
            <span className="text-[#d4845a] font-mono font-bold shrink-0">4-hop</span>
            <span>Maximum depth — comprehensive knowledge graph (slower)</span>
          </div>
        </div>
      </div>

      {/* Query input — uiverse.io style dark input */}
      <div>
        <label className="block text-[10px] text-[#5a6478] mb-1.5">Query</label>
        <div className="relative">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder='e.g. "tubulin", "Aspirin", "EGFR"'
            className="w-full bg-[#0a0c12] border border-[#2a2d35] rounded-lg px-3 py-2.5 text-sm text-[#e1e5ea] placeholder:text-[#40485a] focus:outline-none focus:border-[#3fb950] focus:ring-0 transition-colors"
            disabled={isRunning}
          />
          {isRunning && (
            <span className="absolute right-2.5 top-1/2 -translate-y-1/2 text-[10px] text-[#5fb2c9]">running…</span>
          )}
        </div>
      </div>

      {/* Query Type — segmented control */}
      <div>
        <label className="block text-[10px] text-[#5a6478] mb-1.5">Query Type</label>
        <div className="flex gap-1 bg-[#1c1e26] rounded-lg p-1 border border-[#2a2d35]">
          {(['auto', 'protein', 'ligand'] as const).map((t) => {
            const isActive = queryType === t;
            const label = t === 'auto' ? 'Auto' : t === 'protein' ? 'Protein' : 'Ligand';
            return (
              <button
                key={t}
                type="button"
                onClick={() => setQueryType(t)}
                disabled={isRunning}
                className={`flex-1 py-1.5 rounded-md text-xs font-medium transition-all ${
                  isActive
                    ? 'bg-[#2a2d35] text-[#e1e5ea] shadow-sm'
                    : 'text-[#5a6478] hover:text-[#c8d0dc]'
                }`}
              >
                {label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Hops — segmented control */}
      <div>
        <label className="block text-[10px] text-[#5a6478] mb-1.5">
          Expansion Depth (Hops)
        </label>
        <div className="flex gap-1 bg-[#1c1e26] rounded-lg p-1 border border-[#2a2d35]">
          {[1, 2, 3, 4].map((h) => {
            const isActive = hops === h;
            return (
              <button
                key={h}
                type="button"
                onClick={() => setHops(h)}
                disabled={isRunning}
                className={`flex-1 py-1.5 rounded-md text-xs font-medium transition-all ${
                  isActive
                    ? 'bg-[#2a2d35] text-[#e1e5ea] shadow-sm'
                    : 'text-[#5a6478] hover:text-[#c8d0dc]'
                }`}
              >
                {h}-hop
              </button>
            );
          })}
        </div>
      </div>

      {/* Submit — uiverse.io ghost button style */}
      <button
        type="submit"
        disabled={!query.trim() || isRunning}
        className="w-full py-2.5 rounded-lg font-semibold text-sm transition-all border border-[#3fb950] text-[#3fb950] bg-transparent hover:bg-[#3fb950]/10 active:bg-[#3fb950]/20 disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:bg-transparent disabled:text-[#5a6478] disabled:border-[#3a3d45]"
      >
        {isRunning ? (
          <span className="flex items-center justify-center gap-2">
            <span className="relative flex h-4 w-4">
              <span className="absolute inline-flex h-full w-full rounded-full border-2 border-[#3fb950] border-t-transparent animate-spin" />
            </span>
            Running...
          </span>
        ) : (
          'Run Search'
        )}
      </button>
    </form>
  );
}
