'use client';

import { useEffect, useState, type FormEvent } from 'react';
import { ChevronIcon, SearchIcon, SpinnerIcon } from '@/components/icons';
import {
  btnPrimary,
  cx,
  segmentItemClass,
  segmentWrap,
} from '@/components/ui';
import { authHeaders } from '@/lib/billing';

interface Props {
  onSearch: (query: string, queryType: string, hops: number) => void;
  isRunning: boolean;
}

const QUERY_TYPES = [
  { value: 'auto', label: 'Auto' },
  { value: 'protein', label: 'Protein' },
  { value: 'ligand', label: 'Ligand' },
] as const;

const HOPS = [1, 2, 3, 4] as const;

/* What one more hop buys you. Shown as the helper under the depth control
   because it is the only genuinely non-obvious choice on this screen. */
const HOP_NOTES: Record<number, string> = {
  1: 'Direct relationships only — e.g. Aspirin → COX-1. Fastest.',
  2: 'One step further — e.g. Aspirin → COX-1 → prostaglandin pathway.',
  3: 'Deep network — e.g. adds pathway-associated diseases.',
  4: 'Maximum depth across all databases. Broadest and slowest.',
};

/* One line per depth for the card header: what it adds, not how it is built. */
const HOP_SCOPE: Record<number, string> = {
  1: 'Direct bindings',
  2: 'Plus pathway partners',
  3: 'Plus linked targets',
  4: 'Full network',
};

function formatDuration(seconds: number): string {
  if (seconds < 90) return `${Math.round(seconds)}s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} min`;
  return `${(seconds / 3600).toFixed(1)} h`;
}

export default function SearchForm({ onSearch, isRunning }: Props) {
  const [query, setQuery] = useState('');
  const [queryType, setQueryType] = useState('auto');
  const [hops, setHops] = useState(1);
  /*
   * Median runtime of this account's own finished searches, per depth. Shown on
   * the cards so the depth choice is informed by measurement instead of a
   * marketing number: a depth that has never been run says so.
   */
  const [measured, setMeasured] = useState<Record<number, number>>({});

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fetch('/api/searches?limit=50', {
          headers: await authHeaders(),
        });
        if (!res.ok) return;
        const rows: Array<{
          hops?: number | null;
          status?: string;
          elapsed_seconds?: number | null;
        }> = await res.json();
        if (!alive || !Array.isArray(rows)) return;

        const byHop = new Map<number, number[]>();
        for (const row of rows) {
          if (row.status !== 'completed') continue;
          const seconds = row.elapsed_seconds;
          const depth = Number(row.hops);
          if (typeof seconds !== 'number' || !Number.isFinite(seconds)) continue;
          if (!Number.isInteger(depth) || depth < 1) continue;
          byHop.set(depth, [...(byHop.get(depth) ?? []), seconds]);
        }

        const next: Record<number, number> = {};
        byHop.forEach((values, depth) => {
          const sorted = [...values].sort((a, b) => a - b);
          const mid = Math.floor(sorted.length / 2);
          next[depth] =
            sorted.length % 2 === 0
              ? (sorted[mid - 1] + sorted[mid]) / 2
              : sorted[mid];
        });
        if (alive) setMeasured(next);
      } catch {
        // Timings are a nicety; the control works without them.
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const hasMeasured = Object.keys(measured).length > 0;

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (!query.trim() || isRunning) return;
    onSearch(query.trim(), queryType, hops);
  };

  return (
    <form onSubmit={handleSubmit} className="panel p-5">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="text-sm font-semibold text-fg">New search</h2>
        <span className="text-[11px] text-fg-subtle">
          {isRunning ? 'A search is running' : 'Ready'}
        </span>
      </div>

      {/* Reference material, collapsed by default: it explains the two controls
          below rather than being part of running a search. */}
      <details className="group mt-3 rounded-lg border border-line bg-surface-2">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-2 rounded-lg px-3 py-2.5 text-xs font-medium text-fg-muted transition-colors hover:text-fg">
          How it works, and what a hop is
          <ChevronIcon className="h-4 w-4 shrink-0 transition-transform group-open:rotate-180" />
        </summary>
        <div className="border-t border-line px-3 py-3">
          <ol className="space-y-2 text-xs leading-relaxed text-fg-muted">
            <li className="flex gap-2">
              <span className="font-semibold text-accent">1.</span>
              <span>
                Enter a <strong className="font-medium text-fg">protein</strong>{' '}
                (for example &ldquo;tubulin&rdquo; or &ldquo;EGFR&rdquo;) or a{' '}
                <strong className="font-medium text-fg">compound</strong> (for
                example &ldquo;Aspirin&rdquo;).
              </span>
            </li>
            <li className="flex gap-2">
              <span className="font-semibold text-accent">2.</span>
              <span>
                Leave the type on <strong className="font-medium text-fg">Auto</strong>{' '}
                to let the engine detect it, or set it yourself.
              </span>
            </li>
            <li className="flex gap-2">
              <span className="font-semibold text-accent">3.</span>
              <span>
                Choose an <strong className="font-medium text-fg">expansion depth</strong>{' '}
                — how many relationship steps to follow.
              </span>
            </li>
            <li className="flex gap-2">
              <span className="font-semibold text-accent">4.</span>
              <span>
                Run the search and watch the live log; download the exports when
                it finishes.
              </span>
            </li>
          </ol>
          <dl className="mt-3 space-y-1.5 border-t border-line pt-3">
            {HOPS.map((h) => (
              <div key={h} className="flex gap-2.5 text-xs">
                <dt className="w-14 shrink-0 font-mono text-[11px] font-medium text-accent">
                  {h}-hop
                </dt>
                <dd className="leading-relaxed text-fg-muted">{HOP_NOTES[h]}</dd>
              </div>
            ))}
          </dl>
        </div>
      </details>

      <div className="mt-5">
        <label htmlFor="search-query" className="label">
          Query
        </label>
        <input
          id="search-query"
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="tubulin, Aspirin, EGFR…"
          autoComplete="off"
          spellCheck={false}
          disabled={isRunning}
          className="field"
        />
      </div>

      <div className="mt-4">
        <span id="query-type-label" className="label">
          Query type
        </span>
        <div
          role="group"
          aria-labelledby="query-type-label"
          className={segmentWrap}
        >
          {QUERY_TYPES.map((t) => (
            <button
              key={t.value}
              type="button"
              aria-pressed={queryType === t.value}
              onClick={() => setQueryType(t.value)}
              disabled={isRunning}
              className={segmentItemClass(queryType === t.value)}
            >
              {t.label}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-4">
        <span id="hops-label" className="label">
          Expansion depth
        </span>
        <div
          role="group"
          aria-labelledby="hops-label"
          className="mt-1.5 grid grid-cols-2 gap-1.5"
        >
          {HOPS.map((h) => {
            const active = hops === h;
            const median = measured[h];
            return (
              <button
                key={h}
                type="button"
                aria-pressed={active}
                aria-label={`${h}-hop: ${HOP_SCOPE[h]}${
                  median != null ? `, median ${formatDuration(median)} on your runs` : ''
                }`}
                onClick={() => setHops(h)}
                disabled={isRunning}
                className={cx(
                  'rounded-lg border px-3 py-2 text-left transition-colors',
                  active
                    ? 'border-accent bg-accent-soft'
                    : 'border-line bg-surface-2 hover:border-accent/50',
                  isRunning && 'opacity-60'
                )}
              >
                <span className="flex items-baseline justify-between gap-2">
                  <span
                    className={cx(
                      'text-xs font-semibold',
                      active ? 'text-fg' : 'text-fg-muted'
                    )}
                  >
                    {h} hop{h > 1 ? 's' : ''}
                  </span>
                  <span className="shrink-0 text-[10px] tabular-nums text-fg-subtle">
                    {median != null ? formatDuration(median) : 'not run yet'}
                  </span>
                </span>
                <span className="mt-0.5 block text-[11px] leading-snug text-fg-muted">
                  {HOP_SCOPE[h]}
                </span>
              </button>
            );
          })}
        </div>
        <p className="mt-2 text-xs leading-relaxed text-fg-subtle">
          {HOP_NOTES[hops]}
        </p>
        {hasMeasured && (
          <p className="mt-1 text-[11px] leading-relaxed text-fg-subtle">
            Times are the median of your own finished searches at that depth.
          </p>
        )}
      </div>

      <button
        type="submit"
        disabled={!query.trim() || isRunning}
        className={cx(btnPrimary, 'mt-5 w-full')}
      >
        {isRunning ? (
          <>
            <SpinnerIcon className="h-4 w-4 animate-spin-slow" />
            Running…
          </>
        ) : (
          <>
            <SearchIcon className="h-4 w-4" />
            Run search
          </>
        )}
      </button>
    </form>
  );
}
