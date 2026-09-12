'use client';

import { useEffect, useRef } from 'react';
import type { SearchState } from '@/lib/types';

interface Props {
  search: SearchState;
  logLines?: string[];
}

const statusConfig = {
  queued: { color: 'text-yellow-400', icon: '⏳' },
  running: { color: 'text-cyan-400', icon: '🔄' },
  completed: { color: 'text-green-400', icon: '✅' },
  failed: { color: 'text-red-400', icon: '❌' },
} as const;

/*
 * Semantic log tones — ≤4 accents, weight/position carry the rest.
 * Order matters: first matching rule wins.
 */
type Tone = 'default' | 'structure' | 'phase' | 'ok' | 'warn' | 'err' | 'link';

const TONE_CLASS: Record<Tone, string> = {
  default: 'text-gray-300',
  structure: 'text-cyan-300/90',
  phase: 'text-violet-300',
  ok: 'text-emerald-400',
  warn: 'text-amber-400',
  err: 'text-red-400',
  link: 'text-blue-300 underline decoration-blue-300/30 underline-offset-2',
};

const TONE_RULES: [RegExp, Tone][] = [
  [/error|exception|traceback|failed/i, 'err'],
  [/\[OK\]|✅|✔/, 'ok'],
  [/warn|deprecat|⚠️|❌/i, 'warn'],
  [/^\s*[╔║╚═]/, 'structure'],
  [/^\s*\[\d+\s*\/\s*\d+\]/, 'phase'],
  [/https?:\/\/\S+/, 'link'],
];

function tone(line: string): Tone {
  for (const [re, t] of TONE_RULES) {
    if (re.test(line)) return t;
  }
  return 'default';
}

export default function ProgressView({ search, logLines }: Props) {
  const cfg = statusConfig[search.status] || statusConfig.queued;

  // Empty logLines from a parent stream must not blank out real log data.
  const lines = logLines?.length ? logLines : search.log;

  // Stick-to-bottom auto-scroll that respects user intent: once the user
  // scrolls up to read, we stop forcing them back down.
  const logRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true);

  useEffect(() => {
    const el = logRef.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [lines.length]);

  const onScroll = () => {
    const el = logRef.current;
    if (!el) return;
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48;
  };

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden glow-border">
      {/* Status Header */}
      <div className="px-5 py-4 border-b border-gray-800">
        <div className="flex items-center justify-between mb-1">
          <div className="flex items-center gap-2">
            <span className={`text-lg ${search.status === 'queued' ? 'opacity-60' : ''}`}>
              {cfg.icon}
            </span>
            <span className={`font-semibold ${cfg.color}`}>
              {search.status.charAt(0).toUpperCase() + search.status.slice(1)}
            </span>
          </div>
          <span className="text-xs text-gray-500 font-mono">{search.search_id}</span>
        </div>
        <div className="flex items-center gap-2 text-sm">
          <span className="text-cyan-400 font-medium truncate">{search.query}</span>
          <span className="text-gray-600">·</span>
          <span className="text-gray-400">{search.hops}-hop</span>
          {search.elapsed_seconds && (
            <>
              <span className="text-gray-600">·</span>
              <span className="text-gray-400 tabular-nums">
                {search.elapsed_seconds.toFixed(1)}s
              </span>
            </>
          )}
        </div>
      </div>

      {/* Error surfaced directly under the header — the one thing a user
          must see, never buried at the bottom of a long log. */}
      {search.error && (
        <div className="mx-4 mt-3 p-3 bg-red-950/50 border border-red-900 rounded-lg text-sm text-red-400" role="alert">
          {search.error}
        </div>
      )}

      {/* Progress bar: real indeterminate traveling segment while working;
          no frozen fake 40%. */}
      {(search.status === 'running' || search.status === 'queued') && (
        <div
          className="relative h-[3px] overflow-hidden bg-white/[0.06] mt-3"
          role="progressbar"
          aria-valuetext={search.progress ?? search.status}
        >
          <div className="absolute inset-y-0 w-1/3 rounded-full bg-cyan-400/70 animate-indeterminate" />
        </div>
      )}

      {/* Progress message */}
      {search.progress && (
        <div className="px-5 py-2 bg-gray-950/50 border-b border-gray-800">
          <p className="text-sm text-cyan-300 font-mono">{search.progress}</p>
          {(search.status === 'queued' ||
            (search.status === 'running' &&
              /queued|starting|contacting/i.test(search.progress))) && (
            <p className="text-xs text-gray-400 mt-1">
              The search engine wakes from idle on the first request — this can
              take up to a minute. Subsequent searches start instantly.
            </p>
          )}
        </div>
      )}

      {/* Log Output */}
      <div
        ref={logRef}
        onScroll={onScroll}
        className="p-4 bg-gray-950 overflow-y-auto max-h-[60vh] font-mono"
        aria-live={search.status === 'running' ? 'polite' : undefined}
      >
        {lines.length === 0 && search.status === 'running' && (
          <p className="text-gray-400 text-sm">Waiting for output...</p>
        )}
        {lines.length === 0 && search.status === 'completed' && (
          <p className="text-sm text-gray-500">No output captured.</p>
        )}
        {lines.map((line, i) => (
          <div
            key={i}
            className={`text-[13px] leading-[1.55] whitespace-pre-wrap tabular-nums ${
              i === lines.length - 1 && search.status === 'running'
                ? 'animate-line-in'
                : ''
            } ${TONE_CLASS[tone(line)]}`}
          >
            {line || '\u00A0'}
          </div>
        ))}
        {search.status === 'running' && (
          <div className="flex items-center gap-1.5 mt-2">
            <span className="w-2 h-2 bg-cyan-500 rounded-full animate-pulse" />
            <span className="text-xs text-gray-400">Running...</span>
          </div>
        )}
      </div>
    </div>
  );
}
