import type { SearchStatus } from '../types';

interface Props {
  search: SearchStatus;
  logLines: string[];
}

export default function ProgressView({ search, logLines }: Props) {
  const statusColor = {
    queued: 'text-yellow-400',
    running: 'text-cyan-400',
    completed: 'text-green-400',
    failed: 'text-red-400',
  }[search.status];

  const statusIcon = {
    queued: '⏳',
    running: '🔄',
    completed: '✅',
    failed: '❌',
  }[search.status];

  const logEndRef = (el: HTMLDivElement | null) => {
    if (el) el.scrollIntoView({ behavior: 'smooth' });
  };

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden glow-border">
      {/* Status Header */}
      <div className="p-4 border-b border-gray-800">
        <div className="flex items-center justify-between mb-1">
          <div className="flex items-center gap-2">
            <span className="text-lg">{statusIcon}</span>
            <span className={`font-semibold ${statusColor}`}>
              {search.status.charAt(0).toUpperCase() + search.status.slice(1)}
            </span>
          </div>
          <span className="text-xs text-gray-500 font-mono">
            {search.search_id}
          </span>
        </div>
        <div className="flex items-center gap-3 text-sm">
          <span className="text-cyan-400 font-medium truncate">{search.query}</span>
          <span className="text-gray-600">·</span>
          <span className="text-gray-400">{search.hops}-hop</span>
          {search.elapsed_seconds && (
            <>
              <span className="text-gray-600">·</span>
              <span className="text-gray-400">{search.elapsed_seconds.toFixed(1)}s</span>
            </>
          )}
        </div>
      </div>

      {/* Progress Bar */}
      {(search.status === 'running' || search.status === 'queued') && (
        <div className="h-1 bg-gray-800">
          <div className="h-full bg-cyan-500 animate-pulse rounded-r-full" style={{ width: '40%' }} />
        </div>
      )}

      {/* Progress message */}
      {search.progress && (
        <div className="px-4 py-2 bg-gray-950/50 border-b border-gray-800">
          <p className="text-sm text-cyan-300 font-mono">{search.progress}</p>
        </div>
      )}

      {/* Log Output */}
      <div
        className="p-4 bg-gray-950 overflow-y-auto max-h-[60vh] scrollbar-thin"
        style={{ fontFamily: "'JetBrains Mono', 'Fira Code', monospace" }}
      >
        {logLines.length === 0 && search.status === 'running' && (
          <p className="text-gray-600 text-sm animate-pulse">Waiting for output...</p>
        )}
        {logLines.map((line, i) => (
          <div
            key={i}
            className="text-[13px] leading-relaxed"
            style={{
              color: line.includes('Error')
                ? '#f87171'
                : line.includes('✅') || line.includes('[OK]')
                ? '#4ade80'
                : line.includes('⚠️') || line.includes('❌')
                ? '#fb923c'
                : line.includes('╔══') || line.includes('║') || line.includes('╚')
                ? '#67e8f9'
                : line.includes('[*]') || line.includes('[1/6]') || line.includes('[2/6]') || line.includes('[3/6]') || line.includes('[4/6]') || line.includes('[5/6]') || line.includes('[6/6]')
                ? '#c084fc'
                : line.includes('http') || line.includes('://')
                ? '#60a5fa'
                : '#d1d5db',
            }}
          >
            {line || '\u00A0'}
          </div>
        ))}
        {search.status === 'running' && (
          <div className="flex items-center gap-1.5 mt-2 text-gray-600">
            <span className="w-2 h-2 bg-cyan-500 rounded-full animate-pulse" />
            <span className="text-xs">Running...</span>
          </div>
        )}
        {search.error && (
          <div className="mt-2 p-2 bg-red-950/50 border border-red-900 rounded-lg text-sm text-red-400">
            {search.error}
          </div>
        )}
        <div ref={logEndRef} />
      </div>
    </div>
  );
}
