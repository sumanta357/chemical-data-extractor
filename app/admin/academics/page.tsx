'use client';

/**
 * Academic customers directory (admin).
 *
 * Filter and sort the Academic plan customers by academic email, institute
 * name, institute ID, phone and type of academician. Sorting/filtering happens
 * in Postgres via GET /api/admin/academics — this page never receives the
 * whole table, only the current page. The admin token is kept in
 * localStorage so it is not retyped on every visit, and is sent as a header
 * rather than in the query string.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { AcademicRecord, listAcademics } from '@/lib/billing';
import { SiteFooter, SiteHeader } from '@/components/SiteChrome';
import { SpinnerIcon } from '@/components/icons';
import { btnPrimary, btnSecondary, cx, note } from '@/components/ui';

const TOKEN_KEY = 'scigraph_admin_token';

type SortKey =
  | 'academic_email'
  | 'institute_name'
  | 'institute_id'
  | 'phone'
  | 'academician_type'
  | 'email'
  | 'created_at'
  | 'searches_used';

const COLUMNS: { key: SortKey; label: string }[] = [
  { key: 'email', label: 'Account email' },
  { key: 'academic_email', label: 'Academic email' },
  { key: 'institute_name', label: 'Institute' },
  { key: 'institute_id', label: 'Institute ID' },
  { key: 'phone', label: 'Phone' },
  { key: 'academician_type', label: 'Type' },
  { key: 'searches_used', label: 'Searches' },
  { key: 'created_at', label: 'Joined' },
];

export default function AcademicsDirectoryPage() {
  const [token, setToken] = useState('');
  const [authed, setAuthed] = useState(false);
  const [rows, setRows] = useState<AcademicRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [types, setTypes] = useState<string[]>([]);
  const [filters, setFilters] = useState({
    q: '',
    academic_email: '',
    institute_name: '',
    institute_id: '',
    phone: '',
    academician_type: '',
  });
  const [sort, setSort] = useState<SortKey>('created_at');
  const [order, setOrder] = useState<'asc' | 'desc'>('desc');
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const pageSize = 25;

  useEffect(() => {
    const saved = window.localStorage.getItem(TOKEN_KEY);
    if (saved) {
      setToken(saved);
      setAuthed(true);
    }
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params: Record<string, string> = {
        sort,
        order,
        limit: String(pageSize),
        offset: String(page * pageSize),
      };
      Object.entries(filters).forEach(([key, value]) => {
        if (value.trim()) params[key] = value.trim();
      });
      const res = await listAcademics(params, token);
      setRows(res.academics);
      setTotal(res.total);
      setTypes(res.academician_types);
    } catch (err: any) {
      setError(err.message || 'Could not load the directory');
      if (err.status === 401 || err.status === 404) setAuthed(false);
    } finally {
      setLoading(false);
    }
  }, [filters, order, page, sort, token]);

  useEffect(() => {
    if (authed) void load();
  }, [authed, load]);

  const pages = Math.max(1, Math.ceil(total / pageSize));
  const summary = useMemo(
    () => `${total} academic customer${total === 1 ? '' : 's'}`,
    [total],
  );

  if (!authed) {
    return (
      <div className="flex min-h-screen flex-col">
        <SiteHeader current="app" />
        <main className="flex flex-1 items-center justify-center px-4 py-12">
          <div className="panel w-full max-w-sm p-5">
            <h1 className="text-lg font-semibold text-fg">Academic directory</h1>
            <p className="mt-1 text-sm leading-relaxed text-fg-muted">
              Enter the admin token to view academic customers. It is stored in
              this browser only.
            </p>
            <div className="mt-4">
              <label htmlFor="admin-token" className="label">
                Admin token
              </label>
              <input
                id="admin-token"
                type="password"
                autoComplete="off"
                value={token}
                onChange={(e) => setToken(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    window.localStorage.setItem(TOKEN_KEY, token);
                    setAuthed(true);
                  }
                }}
                className="field"
              />
            </div>
            {error && <p className={cx(note('danger'), 'mt-3')}>{error}</p>}
            <button
              onClick={() => {
                window.localStorage.setItem(TOKEN_KEY, token);
                setAuthed(true);
              }}
              className={`${btnPrimary} mt-4 w-full`}
            >
              Unlock directory
            </button>
          </div>
        </main>
        <SiteFooter />
      </div>
    );
  }

  return (
    <div className="flex min-h-screen flex-col">
      <SiteHeader current="app" />

      <main className="mx-auto w-full max-w-content flex-1 px-4 py-7 sm:px-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h1 className="text-lg font-semibold text-fg">Academic customers</h1>
            <p className="mt-0.5 text-sm text-fg-muted tabular-nums">{summary}</p>
          </div>
          <div className="flex items-center gap-2">
            {loading && (
              <span
                className="flex items-center gap-1.5 text-xs text-fg-muted"
                role="status"
              >
                <SpinnerIcon className="h-3.5 w-3.5 animate-spin-slow" />
                Loading
              </span>
            )}
            <button
              onClick={() => {
                window.localStorage.removeItem(TOKEN_KEY);
                setAuthed(false);
                setRows([]);
              }}
              className={btnSecondary}
            >
              Lock
            </button>
          </div>
        </div>

        <section className="panel mt-5 grid grid-cols-1 gap-3 p-4 sm:grid-cols-2 lg:grid-cols-3">
          <div className="lg:col-span-3">
            <label htmlFor="filter-q" className="label">
              Search everything
            </label>
            <input
              id="filter-q"
              value={filters.q}
              onChange={(e) => {
                setPage(0);
                setFilters({ ...filters, q: e.target.value });
              }}
              placeholder="email, institute, phone, type…"
              className="field"
            />
          </div>
          {(
            [
              ['academic_email', 'Academic email', 'filter-academic-email'],
              ['institute_name', 'Institute name', 'filter-institute'],
              ['institute_id', 'Institute ID', 'filter-institute-id'],
              ['phone', 'Phone', 'filter-phone'],
            ] as const
          ).map(([key, label, id]) => (
            <div key={key}>
              <label htmlFor={id} className="label">
                {label}
              </label>
              <input
                id={id}
                value={filters[key]}
                onChange={(e) => {
                  setPage(0);
                  setFilters({ ...filters, [key]: e.target.value });
                }}
                className="field"
              />
            </div>
          ))}
          <div>
            <label htmlFor="filter-type" className="label">
              Type of academician
            </label>
            <select
              id="filter-type"
              value={filters.academician_type}
              onChange={(e) => {
                setPage(0);
                setFilters({ ...filters, academician_type: e.target.value });
              }}
              className="field"
            >
              <option value="">All types</option>
              {types.map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
          </div>
        </section>

        {error && (
          <p className={cx(note('danger'), 'mt-4')} role="alert">
            {error}
          </p>
        )}

        <div className="mt-4 overflow-x-auto rounded-lg border border-line">
          <table className="w-full text-left text-xs">
            <thead className="bg-surface-2 text-fg-muted">
              <tr>
                {COLUMNS.map((col) => (
                  <th key={col.key} scope="col" className="whitespace-nowrap px-3 py-2.5 font-medium">
                    <button
                      type="button"
                      onClick={() => {
                        setPage(0);
                        if (sort === col.key) {
                          setOrder(order === 'asc' ? 'desc' : 'asc');
                        } else {
                          setSort(col.key);
                          setOrder('asc');
                        }
                      }}
                      aria-label={`Sort by ${col.label}`}
                      className={cx(
                        'transition-colors hover:text-accent',
                        sort === col.key && 'text-fg'
                      )}
                    >
                      {col.label}
                      <span aria-hidden className="ml-1 text-accent">
                        {sort === col.key ? (order === 'asc' ? '▲' : '▼') : ''}
                      </span>
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className="border-t border-line hover:bg-surface-2">
                  <td className="px-3 py-2.5 text-fg">
                    {row.email}
                    {!row.email_verified_at && (
                      <span className="ml-2 text-warning">unverified</span>
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-fg-muted">{row.academic_email || '—'}</td>
                  <td className="px-3 py-2.5 text-fg">{row.institute_name || '—'}</td>
                  <td className="px-3 py-2.5 text-fg-muted">{row.institute_id || '—'}</td>
                  <td className="px-3 py-2.5 text-fg-muted">{row.phone || '—'}</td>
                  <td className="px-3 py-2.5 text-fg-muted">{row.academician_type || '—'}</td>
                  <td className="px-3 py-2.5 text-fg-muted tabular-nums">{row.searches_used}</td>
                  <td className="whitespace-nowrap px-3 py-2.5 text-fg-muted">
                    {row.created_at ? new Date(row.created_at).toLocaleDateString() : '—'}
                  </td>
                </tr>
              ))}
              {rows.length === 0 && !loading && (
                <tr>
                  <td colSpan={COLUMNS.length} className="px-3 py-8 text-center text-fg-subtle">
                    No academic customers match these filters.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="mt-4 flex items-center justify-between text-xs text-fg-muted">
          <span className="tabular-nums">
            Page {page + 1} of {pages}
          </span>
          <div className="flex gap-2">
            <button
              disabled={page === 0}
              onClick={() => setPage(Math.max(0, page - 1))}
              className={cx(btnSecondary, 'px-3 py-1.5 text-xs')}
            >
              Previous
            </button>
            <button
              disabled={page + 1 >= pages}
              onClick={() => setPage(page + 1)}
              className={cx(btnSecondary, 'px-3 py-1.5 text-xs')}
            >
              Next
            </button>
          </div>
        </div>
      </main>

      <SiteFooter />
    </div>
  );
}
