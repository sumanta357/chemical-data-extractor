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

const inputClass =
  'w-full bg-transparent border border-gray-800 px-3 py-1.5 text-sm text-gray-200 focus:border-cyan-600 focus:outline-none';

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
      <main className="min-h-screen bg-[#050912] text-gray-300 px-4 py-16">
        <div className="max-w-sm mx-auto space-y-4">
          <h1 className="text-lg font-semibold text-gray-100">Academic directory</h1>
          <p className="text-sm text-gray-500">
            Enter the admin token to view academic customers. It is stored in this browser only.
          </p>
          <input
            type="password"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            placeholder="Admin token"
            className={inputClass}
          />
          {error && <p className="text-xs text-amber-400">{error}</p>}
          <button
            onClick={() => {
              window.localStorage.setItem(TOKEN_KEY, token);
              setAuthed(true);
            }}
            className="w-full text-sm font-medium py-2 border border-cyan-600 text-cyan-400 hover:bg-cyan-500/10 transition-colors"
          >
            Unlock directory
          </button>
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-[#050912] text-gray-300 px-4 py-10">
      <div className="max-w-6xl mx-auto space-y-5">
        <header className="flex flex-wrap items-baseline justify-between gap-2">
          <h1 className="text-lg font-semibold text-gray-100">Academic customers</h1>
          <div className="flex items-center gap-3 text-xs text-gray-500">
            <span>{summary}</span>
            <button
              onClick={() => {
                window.localStorage.removeItem(TOKEN_KEY);
                setAuthed(false);
                setRows([]);
              }}
              className="underline hover:text-gray-300"
            >
              Lock
            </button>
          </div>
        </header>

        <section className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 border border-gray-800 p-4">
          <div className="lg:col-span-3">
            <label className="block text-xs text-gray-500 mb-1">Search everything</label>
            <input
              value={filters.q}
              onChange={(e) => {
                setPage(0);
                setFilters({ ...filters, q: e.target.value });
              }}
              placeholder="email, institute, phone, type…"
              className={inputClass}
            />
          </div>
          {(
            [
              ['academic_email', 'Academic email'],
              ['institute_name', 'Institute name'],
              ['institute_id', 'Institute ID'],
              ['phone', 'Phone'],
            ] as const
          ).map(([key, label]) => (
            <div key={key}>
              <label className="block text-xs text-gray-500 mb-1">{label}</label>
              <input
                value={filters[key]}
                onChange={(e) => {
                  setPage(0);
                  setFilters({ ...filters, [key]: e.target.value });
                }}
                className={inputClass}
              />
            </div>
          ))}
          <div>
            <label className="block text-xs text-gray-500 mb-1">Type of academician</label>
            <select
              value={filters.academician_type}
              onChange={(e) => {
                setPage(0);
                setFilters({ ...filters, academician_type: e.target.value });
              }}
              className={inputClass}
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

        {error && <p className="text-xs text-amber-400">{error}</p>}

        <div className="overflow-x-auto border border-gray-800">
          <table className="w-full text-left text-xs">
            <thead className="text-gray-500 border-b border-gray-800">
              <tr>
                {COLUMNS.map((col) => (
                  <th key={col.key} className="px-3 py-2 font-medium whitespace-nowrap">
                    <button
                      onClick={() => {
                        setPage(0);
                        if (sort === col.key) {
                          setOrder(order === 'asc' ? 'desc' : 'asc');
                        } else {
                          setSort(col.key);
                          setOrder('asc');
                        }
                      }}
                      className="hover:text-cyan-400"
                    >
                      {col.label}
                      {sort === col.key ? (order === 'asc' ? ' ▲' : ' ▼') : ''}
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className="border-b border-gray-900/70 hover:bg-white/[0.02]">
                  <td className="px-3 py-2 text-gray-300">
                    {row.email}
                    {!row.email_verified_at && (
                      <span className="ml-2 text-amber-500/80">unverified</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-gray-400">{row.academic_email || '—'}</td>
                  <td className="px-3 py-2 text-gray-300">{row.institute_name || '—'}</td>
                  <td className="px-3 py-2 text-gray-400">{row.institute_id || '—'}</td>
                  <td className="px-3 py-2 text-gray-400">{row.phone || '—'}</td>
                  <td className="px-3 py-2 text-gray-400">{row.academician_type || '—'}</td>
                  <td className="px-3 py-2 text-gray-400 tabular-nums">{row.searches_used}</td>
                  <td className="px-3 py-2 text-gray-500 whitespace-nowrap">
                    {row.created_at ? new Date(row.created_at).toLocaleDateString() : '—'}
                  </td>
                </tr>
              ))}
              {rows.length === 0 && !loading && (
                <tr>
                  <td colSpan={COLUMNS.length} className="px-3 py-6 text-center text-gray-600">
                    No academic customers match these filters.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="flex items-center justify-between text-xs text-gray-500">
          <span>
            Page {page + 1} of {pages}
          </span>
          <div className="flex gap-2">
            <button
              disabled={page === 0}
              onClick={() => setPage(Math.max(0, page - 1))}
              className="px-3 py-1 border border-gray-800 disabled:opacity-40"
            >
              Previous
            </button>
            <button
              disabled={page + 1 >= pages}
              onClick={() => setPage(page + 1)}
              className="px-3 py-1 border border-gray-800 disabled:opacity-40"
            >
              Next
            </button>
          </div>
        </div>
      </div>
    </main>
  );
}
