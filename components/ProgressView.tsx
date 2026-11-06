'use client';

import { useEffect, useRef } from 'react';
import { AlertIcon } from '@/components/icons';
import { STATUS_LABEL, cx, note, statusKey, statusPill } from '@/components/ui';
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

/*
 * The engine prints its own step headers ("── Step 2/6: Entity Resolution &
 * Deduplication"), so these are short labels for the same steps — the current
 * one always shows the engine's own wording, these only fill in the others.
 */
const STEP_LABELS: Record<number, string> = {
  1: 'Routing',
  2: 'Resolution',
  3: 'Connections',
  4: 'Analytics',
  5: 'Insights',
  6: 'Exports',
};

export default function ProgressView({ search, logLines }: Props) {
  const key = statusKey(search.status);
  const running = key === 'running' || key === 'queued';

  /* True when this job is queued behind other searches rather than waiting
     for a cold container. The engine sets this exact wording in
     python-api/app.py when every search slot is already taken. */
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

      {/* The engine runs several jobs at once but has a finite number of
          slots, so "queued" has two very different causes. Only the
          cold-start one is worth explaining here — telling someone the
          engine is waking when it is awake and busy sends them looking at
          the wrong problem. */}
      {search.progress && busyBehindOther && (
        <p className="mx-5 mt-3 text-xs leading-relaxed text-fg-muted">
          The engine is awake and already running other searches, and all of
          its slots are taken. This one starts as soon as one frees up — the
          engine has not failed.
        </p>
      )}
      {search.progress && coldStart && !busyBehindOther && (
        <p className="mx-5 mt-3 text-xs leading-relaxed text-fg-muted">
          The search engine wakes from idle on the first request — this can take
          up to a minute. Later searches start immediately.
        </p>
      )}

      {/* Where the run actually is. The engine reports its own step number, so
          this is a measurement of the pipeline, not an invented percentage. */}
      {typeof search.step === 'number' && (
        <div className="mx-5 mt-3">
          <div className="flex items-baseline justify-between gap-3">
            <span className="truncate text-[11px] font-medium text-fg-muted">
              {search.step_label ?? STEP_LABELS[search.step] ?? `Step ${search.step}`}
            </span>
            <span className="shrink-0 text-[11px] tabular-nums text-fg-subtle">
              Step {search.step} of {search.step_total ?? 6}
            </span>
          </div>
          <ol className="mt-1.5 flex gap-1" aria-label="Engine pipeline steps">
            {Array.from(
              { length: search.step_total ?? 6 },
              (_, i) => i + 1
            ).map((n) => (
              <li
                key={n}
                className="min-w-0 flex-1"
                aria-current={n === search.step ? 'step' : undefined}
              >
                <div
                  className={cx(
                    'h-1 rounded-full transition-colors',
                    n < search.step!
                      ? 'bg-accent/60'
                      : n === search.step
                        ? 'bg-accent'
                        : 'bg-surface-3'
                  )}
                />
                <div
                  className={cx(
                    'mt-1 truncate text-[10px]',
                    n === search.step ? 'text-fg-muted' : 'text-fg-subtle'
                  )}
                >
                  {STEP_LABELS[n] ?? `Step ${n}`}
                </div>
              </li>
            ))}
          </ol>
        </div>
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
