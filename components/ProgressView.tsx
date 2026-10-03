'use client';

import { useEffect, useRef } from 'react';
import type { SearchState } from '@/lib/types';

interface Props {
  search: SearchState;
  logLines?: string[];
}

const statusConfig = {
  queued: { color: 'text-yellow-400', label: 'Queued' },
  running: { color: 'text-cyan-400', label: 'Running' },
  completed: { color: 'text-green-400', label: 'Completed' },
  failed: { color: 'text-red-400', label: 'Failed' },
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

  /* True when this job is queued behind another search rather than waiting
     for a cold container. The engine sets this exact wording in
     python-api/app.py when the single search slot is already taken. */
  const busyBehindOther =
    !!search.progress && /waiting for another search/i.test(search.progress);

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
            <span className={`text-xs font-semibold uppercase tracking-wider px-2 py-0.5 rounded border ${cfg.color} border-current bg-current/5`}>
              {cfg.label}
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
          {/* The engine runs one job at a time, so "queued" has two very
              different causes. Only the cold-start one is worth explaining
              here — telling someone the engine is waking when it is awake and
              busy sends them looking at the wrong problem. */}
          {busyBehindOther ? (
            <p className="text-xs text-gray-400 mt-1">
              The engine is awake and already running another search. Only one
              search runs at a time, so this one starts as soon as that
              finishes — the engine has not failed.
            </p>
          ) : (
            !busyBehindOther &&
            (search.status === 'queued' ||
              (search.status === 'running' &&
                /queued|starting|contacting/i.test(search.progress ?? ''))) && (
              <p className="text-xs text-gray-400 mt-1">
                The search engine wakes from idle on the first request — this
                can take up to a minute. Subsequent searches start instantly.
              </p>
            )
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
          <div className="mt-2">
            <span className="text-xs text-gray-400">Working — output streams below as it arrives</span>
          </div>
        )}
      </div>
    </div>
  );
}
