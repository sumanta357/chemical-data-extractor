'use client';

import { useCallback, useEffect, useState } from 'react';
import { AlertIcon, ArrowRightIcon, SpinnerIcon } from '@/components/icons';
import { STATUS_LABEL, btnSecondary, cx, note, statusKey, statusPill } from '@/components/ui';
import { authHeaders } from '@/lib/billing';
import type { SearchSummary } from '@/lib/types';

interface Props {
  onViewSearch: (searchId: string) => void;
}

function formatWhen(iso: string) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '';
  const today = new Date();
  const sameDay = date.toDateString() === today.toDateString();
  const time = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  return sameDay ? `Today ${time}` : `${date.toLocaleDateString()} ${time}`;
}

export default function HistoryPanel({ onViewSearch }: Props) {
  const [searches, setSearches] = useState<SearchSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      // The list is scoped to the caller's account, so it must carry their
      // token. Without it the engine answers [] and every signed-in user's
      // history is silently replaced by "No searches yet".
      const res = await fetch('/api/searches?limit=20', {
        headers: await authHeaders(),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setSearches(await res.json());
    } catch {
      setError('The server did not answer.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <section aria-labelledby="history-heading">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 id="history-heading" className="text-base font-semibold text-fg">
          Search history
        </h2>
        <p className="text-xs text-fg-subtle">
          Your 20 most recent searches, newest first
        </p>
      </div>

      {loading && (
        <div
          className="panel mt-4 flex items-center justify-center gap-2.5 py-16 text-sm text-fg-muted"
          role="status"
        >
          <SpinnerIcon className="h-4 w-4 animate-spin-slow" />
          Loading your searches…
        </div>
      )}

      {!loading && error && (
        <div className="panel mt-4 p-5" role="alert">
          <div className="flex items-start gap-2">
            <AlertIcon className="mt-px h-4 w-4 shrink-0 text-danger" />
            <div>
              <p className="text-sm font-medium text-fg">
                Could not load your search history
              </p>
              <p className="mt-1 text-sm text-fg-muted">
                {error} If you are signed in, try again — and check your
                connection if it keeps failing.
              </p>
              <button type="button" onClick={() => void load()} className={`${btnSecondary} mt-3`}>
                Try again
              </button>
            </div>
          </div>
        </div>
      )}

      {!loading && !error && searches.length === 0 && (
        <div className="panel mt-4 px-5 py-12 text-center">
          <p className="text-sm font-medium text-fg">No searches yet</p>
          <p className="mx-auto mt-1.5 max-w-sm text-sm text-fg-muted">
            Searches you run are listed here with their status, runtime and
            export count. Run one from the Search tab to get started.
          </p>
        </div>
      )}

      {!loading && !error && searches.length > 0 && (
        <ul className="mt-4 space-y-2">
          {searches.map((s) => {
            const key = statusKey(s.status);
            return (
              <li key={s.search_id}>
                <button
                  type="button"
                  onClick={() => onViewSearch(s.search_id)}
                  className="group flex w-full items-center gap-3 rounded-lg border border-line bg-surface px-4 py-3 text-left transition-colors hover:border-line-strong hover:bg-surface-2"
                >
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="truncate text-sm font-medium text-fg">
                        {s.query}
                      </span>
                      <span className={statusPill(s.status)}>{STATUS_LABEL[key]}</span>
                    </span>
                    <span className="mt-1 block truncate font-mono text-[11px] text-fg-subtle">
                      {s.search_id} · {s.file_count}{' '}
                      {s.file_count === 1 ? 'file' : 'files'}
                      {s.elapsed_seconds != null &&
                        ` · ${s.elapsed_seconds.toFixed(1)}s`}
                    </span>
                  </span>
                  <span className="hidden shrink-0 text-xs text-fg-subtle sm:block">
                    {formatWhen(s.created_at)}
                  </span>
                  <ArrowRightIcon className="h-4 w-4 shrink-0 text-fg-subtle transition-colors group-hover:text-accent" />
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {!loading && !error && searches.length > 0 && (
        <p className={cx(note('info'), 'mt-4')}>
          Opening a past search re-loads its status and log. The export files
          themselves are deleted from the server about 30 minutes after that
          search finished.
        </p>
      )}
    </section>
  );
}
