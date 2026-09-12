'use client';

import type { SearchState } from '@/lib/types';

interface Props {
  search: SearchState;
  logLines?: string[];
}

const statusMeta = {
  queued:     { label: 'Queued',     bar: 'bg-[#f0c63a]', pulse: 'bg-[#f0c63a]' },
  running:    { label: 'Running',    bar: 'bg-[#5fb2c9]', pulse: 'bg-[#5fb2c9]' },
  completed:  { label: 'Completed',  bar: 'bg-[#3fb950]', pulse: 'bg-[#3fb950]' },
  failed:     { label: 'Failed',     bar: 'bg-[#f87171]', pulse: 'bg-[#f87171]' },
};

export default function ProgressView({ search, logLines }: Props) {
  const meta = statusMeta[search.status] ?? statusMeta.queued;
  const lines = logLines ?? search.log;

  return (
    <div className="card overflow-hidden">
      {/* Header */}
      <div className="px-4 py-3 border-b border-[rgb(var(--border))] flex items-center justify-between">
        <div className="flex items-center gap-2.5">
          <span className="text-xs font-semibold text-[rgb(var(--muted))] uppercase tracking-wider">
            {meta.label}
          </span>
          <span className="text-xs font-mono text-[rgb(var(--muted))]">{search.search_id}</span>
        </div>
        <div className="text-right">
          <p className="text-sm font-medium text-[rgb(200 208 220)] truncate max-w-[220px]">
            {search.query}
          </p>
          <p className="text-[10px] text-[rgb(var(--muted))] mt-0.5">
            {search.hops}-hop · {search.elapsed_seconds ? search.elapsed_seconds.toFixed(1) + 's' : '—'}
          </p>
        </div>
      </div>

      {/* Progress bar (queued/running only) */}
      {(search.status === 'running' || search.status === 'queued') && (
        <div className="h-1 bg-[rgb(30 33 42)]">
          <div
            className="h-full rounded-r-full transition-all duration-300"
            style={{
              width: search.progress ? Math.min(92, 12 + (search.progress.length % 20) * 4) + '%' : '22%',
              background: meta.bar,
            }}
          />
        </div>
      )}

      {/* Progress message */}
      {search.progress && (
        <div className="px-4 py-2 bg-[rgb(18 21 30)] border-b border-[rgb(var(--border))] flex items-center gap-2">
          <span className="spinner-square shrink-0" />
          <p className="text-xs text-[rgb(150 160 180)] font-mono truncate">{search.progress}</p>
        </div>
      )}

      {/* Log output */}
      <div className="p-4 bg-[rgb(10 12 18)] overflow-y-auto max-h-[60vh] font-mono text-[12px] leading-[1.5]">
        {lines.length === 0 && search.status === 'running' && (
          <p className="text-[rgb(80 90 110)]">Waiting for output…</p>
        )}
        {lines.map((line, i) => {
          const style: React.CSSProperties = {
            color: lineIncludes(line, 'Error')
              ? '#f87171'
              : lineIncludes(line, '[OK]') || lineIncludes(line, '✅')
              ? '#4ade80'
              : lineIncludes(line, '⚠️') || lineIncludes(line, '❌')
              ? '#fb923c'
              : lineIncludes(line, '╔══') || lineIncludes(line, '║') || lineIncludes(line, '╚')
              ? '#67e8f9'
              : lineIncludesAny(line, ['[1/6]', '[2/6]', '[3/6]', '[4/6]', '[5/6]', '[6/6]', '[*]'])
              ? '#c084fc'
              : lineIncludes(line, 'http') || lineIncludes(line, '://')
              ? '#60a5fa'
              : '#d1d5db',
          };
          return (
            <div key={i} className="whitespace-pre-wrap" style={style}>
              {line || '\u00A0'}
            </div>
          );
        })}

        {search.status === 'running' && (
          <div className="flex items-center gap-2 mt-3 text-[rgb(100 110 130)] text-xs">
            <span className="spinner-square" />
            <span className="font-medium">Still running — output will appear here.</span>
          </div>
        )}

        {search.error && (
          <div className="mt-3 p-3 bg-[rgba(248,113,113,0.08)] border border-[rgb(248,113,113)] rounded-lg text-xs text-[#f87171]">
            {search.error}
          </div>
        )}

        <div ref={(el) => el?.scrollIntoView({ behavior: 'smooth' })} />
      </div>
    </div>
  );
}

function lineIncludes(line: string, token: string): boolean {
  return line.indexOf(token) !== -1;
}

function lineIncludesAny(line: string, tokens: string[]): boolean {
  for (let i = 0; i < tokens.length; i += 1) {
    if (line.indexOf(tokens[i]) !== -1) return true;
  }
  return false;
}
