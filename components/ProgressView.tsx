'use client';

import { useEffect, useRef } from 'react';
import { AlertIcon } from '@/components/icons';
import { STATUS_LABEL, note, statusKey, statusPill } from '@/components/ui';
import type { SearchState } from '@/lib/types';

interface Props {
  search: SearchState;
  logLines?: string[];
}

/*
 * Semantic log tones — the colour is a secondary cue only; weight and content
 * carry the meaning, so the log stays readable in both themes.
 * Order matters: first matching rule wins.
 */
type Tone = 'default' | 'structure' | 'phase' | 'ok' | 'warn' | 'err' | 'link';

const TONE_CLASS: Record<Tone, string> = {
  default: 'text-fg-muted',
  structure: 'text-fg-subtle',
  phase: 'text-info',
  ok: 'text-success',
  warn: 'text-warning',
  err: 'text-danger',
  link: 'text-info underline decoration-info/40 underline-offset-2',
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
  const key = statusKey(search.status);
  const running = key === 'running' || key === 'queued';

  /* True when this job is queued behind another search rather than waiting
     for a cold container. The engine sets this exact wording in
     python-api/app.py when the single search slot is already taken. */
  const busyBehindOther =
    !!search.progress && /waiting for another search/i.test(search.progress);

  const coldStart =
    key === 'queued' ||
    (key === 'running' && /queued|starting|contacting/i.test(search.progress ?? ''));

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
    <section className="panel overflow-hidden">
      {/* Run summary */}
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 px-5 py-4">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className={statusPill(search.status)}>{STATUS_LABEL[key]}</span>
            <span className="truncate text-sm font-medium text-fg">
              {search.query}
            </span>
            <span className="text-xs text-fg-subtle">{search.hops}-hop</span>
            {search.elapsed_seconds != null && (
              <span className="text-xs tabular-nums text-fg-subtle">
                {search.elapsed_seconds.toFixed(1)}s
              </span>
            )}
          </div>
          {search.progress && (
            <p className="mt-1.5 font-mono text-xs text-fg-muted">
              {search.progress}
            </p>
          )}
        </div>
        <span className="shrink-0 font-mono text-[11px] text-fg-subtle">
          {search.search_id}
        </span>
      </div>

      {/* Error surfaced directly under the header — the one thing a user
          must see, never buried at the bottom of a long log. */}
      {search.error && (
        <div className="mx-5 mb-4 flex items-start gap-2" role="alert">
          <AlertIcon className="mt-px h-4 w-4 shrink-0" />
          <p className={note('danger')}>{search.error}</p>
        </div>
      )}

      {/* Progress rail: a real indeterminate segment while the job works;
          no frozen fake percentage. */}
      {running && (
        <div
          className="relative h-[3px] overflow-hidden bg-surface-3"
          role="progressbar"
          aria-valuetext={search.progress ?? STATUS_LABEL[key]}
        >
          <div className="absolute inset-y-0 w-1/3 bg-accent/70 animate-indeterminate" />
        </div>
      )}

      {/* The engine runs one job at a time, so "queued" has two very
          different causes. Only the cold-start one is worth explaining
          here — telling someone the engine is waking when it is awake and
          busy sends them looking at the wrong problem. */}
      {search.progress && busyBehindOther && (
        <p className="mx-5 mt-3 text-xs leading-relaxed text-fg-muted">
          The engine is awake and already running another search. Only one
          search runs at a time, so this one starts as soon as that finishes —
          the engine has not failed.
        </p>
      )}
      {search.progress && coldStart && !busyBehindOther && (
        <p className="mx-5 mt-3 text-xs leading-relaxed text-fg-muted">
          The search engine wakes from idle on the first request — this can take
          up to a minute. Later searches start immediately.
        </p>
      )}

      {/* Live log */}
      <div className="border-t border-line bg-sunken">
        <div className="flex items-center justify-between px-5 pt-3">
          <h3 className="text-[11px] font-semibold uppercase tracking-[0.07em] text-fg-subtle">
            Engine log
          </h3>
          {running && (
            <span className="text-[11px] text-fg-subtle">Streaming live</span>
          )}
        </div>
        <div
          ref={logRef}
          onScroll={onScroll}
          className="max-h-[55vh] overflow-y-auto px-5 py-3 font-mono text-[12.5px] leading-[1.6]"
          aria-live={key === 'running' ? 'polite' : undefined}
          aria-label="Search engine log output"
          tabIndex={0}
        >
          {lines.length === 0 && running && (
            <p className="text-fg-subtle">Waiting for output…</p>
          )}
          {lines.length === 0 && key === 'completed' && (
            <p className="text-fg-subtle">No output was captured.</p>
          )}
          {lines.length === 0 && key === 'failed' && (
            <p className="text-fg-subtle">
              The engine stopped before it produced any output.
            </p>
          )}
          {lines.map((line, i) => (
            <div
              key={i}
              className={`whitespace-pre-wrap break-words ${
                i === lines.length - 1 && key === 'running'
                  ? 'animate-line-in'
                  : ''
              } ${TONE_CLASS[tone(line)]}`}
            >
              {line || '\u00A0'}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
