'use client';

import { useEffect, useState } from 'react';
import { AlertIcon, CheckIcon, ChevronIcon } from '@/components/icons';
import { cx } from '@/components/ui';
import {
  getSources,
  limitedSources,
  narrowSources,
  workingCount,
  type DataSourcesResponse,
  type DataSource,
  type SourceStatus,
} from '@/lib/sources';

const STATUS_LABEL: Record<SourceStatus, string> = {
  working: 'Verified working',
  partial: 'Limited',
  unavailable: 'Unavailable',
  unverified: 'Not yet verified',
};

const STATUS_DOT: Record<SourceStatus, string> = {
  working: 'bg-success',
  partial: 'bg-warning',
  unavailable: 'bg-fg-subtle',
  unverified: 'bg-fg-subtle',
};

function statusDescription(source: DataSource): string {
  if (source.note) return source.note;
  if (source.status === 'working') return source.covers;
  return STATUS_LABEL[source.status];
}

/**
 * The empty state's account of what is actually connected.
 *
 * Everything here is read from the engine. A source that is not working is
 * named and explained rather than quietly counted, and if the engine cannot be
 * reached the panel says so instead of falling back to a claim.
 */
export default function DataSourcePanel() {
  const [data, setData] = useState<DataSourcesResponse | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [showAll, setShowAll] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getSources().then((result) => {
      if (cancelled) return;
      setData(result);
      setLoaded(true);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!loaded) {
    return (
      <div className="mt-6 rounded-lg border border-line bg-surface-2 px-4 py-3">
        <p className="text-xs text-fg-subtle">Checking which data sources are available…</p>
      </div>
    );
  }

  if (!data || !data.sources.length) {
    return (
      <div className="mt-6 rounded-lg border border-line bg-surface-2 px-4 py-3">
        <p className="text-xs leading-relaxed text-fg-muted">
          Connected data sources could not be read from the engine right now. Run a
          search to see which sources answered.
        </p>
      </div>
    );
  }

  const working = workingCount(data);
  const limited = limitedSources(data);
  const narrow = narrowSources(data);
  const byGroup = data.groups
    .map((group) => ({
      ...group,
      sources: data.sources.filter((s) => s.group === group.name),
    }))
    .filter((group) => group.sources.length > 0);

  const summary =
    working === data.implemented_total
      ? `All ${working} connected sources returned data in the latest verification.`
      : `${working} of ${data.implemented_total} connected sources returned data in the latest verification.`;

  return (
    <section className="mt-6" aria-labelledby="data-sources-heading">
      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h3 id="data-sources-heading" className="text-sm font-semibold text-fg">
          Connected data sources
        </h3>
        <p className="text-xs text-fg-subtle tabular-nums">
          {working} working · {limited.length} limited · {data.implemented_total}{' '}
          implemented
        </p>
      </div>

      <p className="mt-1.5 text-xs leading-relaxed text-fg-muted">{summary}</p>

      {narrow.length > 0 && (
        <p className="mt-1.5 text-xs leading-relaxed text-fg-muted">
          {narrow.length} of them answer only the identifier format they are
          authoritative for — a Reactome ID, a PDB code, a gene accession. A plain
          name such as &ldquo;aspirin&rdquo; returns nothing from those, which is a
          property of the source, not a fault.
        </p>
      )}

      <ul className="mt-3 grid grid-cols-1 gap-px overflow-hidden rounded-lg border border-line bg-line sm:grid-cols-2">
        {byGroup.map((group) => (
          <li key={group.name} className="bg-surface px-3 py-2.5">
            <p className="text-xs font-medium text-fg">{group.name}</p>
            <ul className="mt-1 space-y-1">
              {group.sources.map((source) => (
                <li key={source.name} className="flex items-start gap-1.5">
                  <span
                    aria-hidden
                    className={cx('mt-[5px] h-1.5 w-1.5 shrink-0 rounded-full', STATUS_DOT[source.status])}
                  />
                  <span className="min-w-0">
                    <span className="text-xs text-fg-muted">{source.name}</span>
                    {source.answers_common_queries === false && (
                      <>
                        <span aria-hidden className="ml-1 text-fg-subtle">
                          †
                        </span>
                        <span className="sr-only">
                          {' '}
                          — answers only its own identifier format, not a plain name
                        </span>
                      </>
                    )}
                    <span className="sr-only"> — {STATUS_LABEL[source.status]}</span>
                  </span>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ul>

      {limited.length > 0 && (
        <div className="mt-3">
          <button
            type="button"
            onClick={() => setShowAll((v) => !v)}
            aria-expanded={showAll}
            className="inline-flex items-center gap-1 text-xs font-medium text-fg-muted transition-colors hover:text-fg"
          >
            <ChevronIcon className={cx('h-3.5 w-3.5 transition-transform', showAll && 'rotate-180')} />
            {showAll ? 'Hide' : 'Show'} {limited.length} source
            {limited.length === 1 ? '' : 's'} with limits
          </button>

          {showAll && (
            <ul className="mt-2 space-y-2">
              {limited.map((source) => (
                <li
                  key={source.name}
                  className={cx(
                    'flex items-start gap-2 rounded-lg border px-3 py-2 text-xs leading-relaxed',
                    source.status === 'unavailable'
                      ? 'border-line bg-surface-2 text-fg-muted'
                      : 'border-warning-line bg-warning-soft text-fg'
                  )}
                >
                  {source.status === 'unavailable' ? (
                    <AlertIcon className="mt-px h-3.5 w-3.5 shrink-0 text-fg-subtle" />
                  ) : (
                    <CheckIcon className="mt-px h-3.5 w-3.5 shrink-0 text-warning" />
                  )}
                  <span>
                    <strong className="font-medium">{source.name}</strong> —{' '}
                    {statusDescription(source)}
                  </span>
                </li>
              ))}
            </ul>
          )}

          {narrow.length > 0 && (
            <p className="mt-2 text-xs leading-relaxed text-fg-subtle">
              Marked with †: connected and verified, but answering only its own
              identifier format.
            </p>
          )}
        </div>
      )}
    </section>
  );
}