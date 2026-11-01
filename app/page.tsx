'use client';

import { useState, useEffect, useRef, useCallback } from 'react';
import SearchForm from '@/components/SearchForm';
import ProgressView from '@/components/ProgressView';
import ResultsView from '@/components/ResultsView';
import HistoryPanel from '@/components/HistoryPanel';
import AIAssistant from '@/components/AIAssistant';
import AccountPanel from '@/components/AccountPanel';
import ConfirmEmailPanel from '@/components/ConfirmEmailPanel';

import { SiteFooter, SiteHeader } from '@/components/SiteChrome';
import { HistoryIcon, SearchIcon } from '@/components/icons';
import { getMe, authHeaders, type Entitlement } from '@/lib/billing';
import type { SearchState } from '@/lib/types';

export default function HomePage() {
  const [search, setSearch] = useState<SearchState | null>(null);
  const [logLines, setLogLines] = useState<string[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [activeTab, setActiveTab] = useState<'search' | 'history'>('search');
  const [accountOpen, setAccountOpen] = useState(false);
  const [entitlement, setEntitlement] = useState<Entitlement | null>(null);
  const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Did we arrive from a Supabase auth callback (the confirmation link)?
  //
  // Read during the first render, before any effect runs, because the Supabase
  // client strips these params off the URL as soon as it exchanges them — by
  // the time an effect looked, the evidence would be gone. Checking the URL
  // itself also means an ordinary visit costs nothing: no fetch, no session
  // lookup, no change to how fast the workspace appears.
  //
  // PKCE callbacks carry ?code=; the implicit flow puts access_token in the
  // fragment. Either means "this visit is the result of an auth link".
  const [fromAuthCallback] = useState(() => {
    if (typeof window === 'undefined') return false;
    return (
      window.location.search.includes('code=') ||
      window.location.hash.includes('access_token')
    );
  });

  const [fromSiteUrlConfirmation] = useState(() => {
    if (typeof window === 'undefined') return false;
    const params = new URLSearchParams(window.location.search);
    // The default Supabase Redirect URL target is the Site URL with this query.
    // Confirmations work without any dashboard Redirect URL change when the
    // project's Site URL is the app origin and this parameter is present.
    return params.get('confirmed') === '1';
  });

  // The callback URL is not proof that the confirmation failed. If the link
  // yields no session -- a PKCE verifier from another browser, cleared storage,
  // or a link already opened once -- drop through to the ordinary page rather
  // than telling someone their verified address is invalid. They can sign in
  // there, which is exactly what they were going to do next.
  const [callbackUnusable, setCallbackUnusable] = useState(false);

  const handleSearch = useCallback(
    async (query: string, queryType: string, hops: number) => {
      setIsRunning(true);
      setLogLines([]);
      setSearch(null);

      try {
        // The engine meters this search to an account, so it must carry the
        // caller's Supabase access token.
        const res = await fetch('/api/search', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...(await authHeaders()) },
          body: JSON.stringify({ query, query_type: queryType, hops }),
        });
        if (!res.ok) {
          const payload = await res.json().catch(() => ({ detail: res.statusText }));
          const detail = payload?.detail;
          // The engine answers 401/402 with {message, code} when accounts or
          // plan limits are enforced — surface the message and open the panel.
          if (detail && typeof detail === 'object') {
            if (detail.code === 'quota_reached' || detail.code === 'auth_required') {
              setAccountOpen(true);
              void getMe().then((r) => setEntitlement(r.entitlement)).catch(() => {});
            }
            throw new Error(detail.message || 'Search failed to start');
          }
          throw new Error(typeof detail === 'string' ? detail : 'Search failed to start');
        }
        const data = await res.json();
        setSearch(data);
        setLogLines(data.log || []);
        startPolling(data.search_id);
      } catch (err: any) {
        setSearch({
          search_id: 'error',
          query,
          query_type: queryType,
          hops,
          status: 'failed',
          progress: 'Failed to start',
          log: [err.message],
          export_files: [],
          export_dir: '',
          created_at: new Date().toISOString(),
          elapsed_seconds: null,
          error: err.message,
        });
        setIsRunning(false);
      }
    },
    []
  );

  const startPolling = useCallback((searchId: string) => {
    let offset = 0;
    let consecutiveErrors = 0;

    pollingRef.current = setInterval(async () => {
      try {
        // A search belongs to the account that started it, so a poll that does
        // not say who it is gets a 404 — which the branch below would report as
        // "the engine restarted". Every poll therefore carries the caller's
        // Supabase token, exactly as the submit that created the search did.
        const [statusRes, logRes] = await Promise.all([
          fetch(`/api/search/${searchId}`, { headers: await authHeaders() }),
          fetch(`/api/search/${searchId}/log?offset=${offset}`, {
            headers: await authHeaders(),
          }),
        ]);

        if (statusRes.status === 404) {
          // The engine no longer has this job: the container was replaced while
          // it ran (deploy, free-tier recycle) or it was dropped for memory.
          // Fail honestly instead of freezing at "Queued..." forever.
          if (pollingRef.current) clearInterval(pollingRef.current);
          pollingRef.current = null;
          setSearch((prev) =>
            prev && prev.search_id === searchId
              ? {
                  ...prev,
                  status: 'failed',
                  error:
                    'This search was lost because the server it was running on was restarted or recycled. Free-tier instances are replaced after a deploy or after idling, and a running search does not survive. Please run the search again.',
                  progress: 'Search lost — engine restarted',
                }
              : prev
          );
          setIsRunning(false);
          return;
        }

        if (!statusRes.ok) {
          throw new Error(`HTTP ${statusRes.status}`);
        }

        consecutiveErrors = 0;
        const status = await statusRes.json();
        setSearch(status);
        setLogLines(status.log || []);

        if (logRes.ok) {
          const logData = await logRes.json();
          if (logData.new_lines?.length > 0) {
            offset = logData.total_lines;
          }
        }

        if (status.status === 'completed' || status.status === 'failed') {
          if (pollingRef.current) clearInterval(pollingRef.current);
          pollingRef.current = null;
          setIsRunning(false);
          // Usage was counted server-side when the job completed — refresh the
          // header so the remaining-searches figure stays accurate.
          if (status.status === 'completed') {
            void getMe().then((r) => setEntitlement(r.entitlement)).catch(() => {});
          }
        }
      } catch {
        // One failed poll (network blip, brief proxy hiccup) should not kill
        // the run — only give up after several consecutive failures.
        consecutiveErrors += 1;
        if (consecutiveErrors >= 6) {
          if (pollingRef.current) clearInterval(pollingRef.current);
          pollingRef.current = null;
          setSearch((prev) =>
            prev && prev.search_id === searchId
              ? {
                  ...prev,
                  status: 'failed',
                  error:
                    'Lost contact with the search engine while running. Check your connection and run the search again.',
                  progress: 'Connection lost',
                }
              : prev
          );
          setIsRunning(false);
        }
      }
    }, 1000);
  }, []);

  useEffect(() => {
    return () => {
      if (pollingRef.current) clearInterval(pollingRef.current);
    };
  }, []);

  const handleViewSearch = useCallback(async (searchId: string) => {
    // Owner-scoped like the poll: without the token this 404s and the history
    // click-through silently loads nothing.
    fetch(`/api/search/${searchId}`, { headers: await authHeaders() })
      .then((res) => res.json())
      .then((data) => {
        setSearch(data);
        setLogLines(data.log || []);
        setActiveTab('search');
      })
      .catch(() => {});
  }, []);

  const tabs = (
    <>
      {(
        [
          { id: 'search', label: 'Search', Icon: SearchIcon },
          { id: 'history', label: 'History', Icon: HistoryIcon },
        ] as const
      ).map(({ id, label, Icon }) => {
        const active = activeTab === id;
        return (
          <button
            key={id}
            type="button"
            onClick={() => setActiveTab(id)}
            aria-current={active ? 'page' : undefined}
            className={`inline-flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-1.5 text-sm font-medium transition-colors ${
              active
                ? 'bg-surface-3 text-fg'
                : 'text-fg-muted hover:bg-surface-2 hover:text-fg'
            }`}
          >
            <Icon className="h-4 w-4" />
            {label}
          </button>
        );
      })}
    </>
  );

  const hasResults = search?.status === 'completed' && search.export_files.length > 0;

  // Placed after every hook so none can be skipped by the early return. The
  // panel bootstraps the Supabase client itself, which is what lets it read
  // the session out of the confirmation link. This renders for either entry
  // point: the dashboard Redirect URL (/?confirmed=1) and the dedicated
  // /confirm-email path when it has been added there.
  const fromConfirmationLink =
    fromAuthCallback || fromSiteUrlConfirmation;

  if (fromConfirmationLink && !callbackUnusable) {
    return (
      <div className="flex min-h-screen flex-col">
        <SiteHeader current="app" />

        <main className="flex flex-1 items-center justify-center px-4 py-12">
          <ConfirmEmailPanel onNoSession={() => setCallbackUnusable(true)} />
        </main>

        <SiteFooter />
      </div>
    );
  }

  return (
    <div className="flex min-h-screen flex-col">
      <SiteHeader
        current="app"
        nav={tabs}
        accountSlot={
          <AccountPanel
            open={accountOpen}
            onOpen={() => setAccountOpen(true)}
            onClose={() => setAccountOpen(false)}
            onChange={setEntitlement}
          />
        }
      />

      <main className="mx-auto w-full max-w-content flex-1 px-4 py-7 sm:px-6 sm:py-9">
        {/* Page introduction. Left-aligned and short: this is a tool, so the
            query box stays within one screen of the top. */}
        <div className="max-w-3xl">
          <h1 className="text-2xl font-semibold tracking-tight text-fg sm:text-[28px] sm:leading-[1.2]">
            Build a multi-hop knowledge graph from any protein or compound
          </h1>
          <p className="mt-2 text-[15px] leading-relaxed text-fg-muted">
            Chemical Data Extractor traverses public chemistry, proteomics and
            pathway sources from a single query, then exports the assembled
            graph as a workbook, CSV, GraphML, Cypher and more.
          </p>
          <p className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-fg-subtle">
            <span className="rounded border border-line bg-surface-2 px-1.5 py-0.5 font-medium">
              Up to 4 hops
            </span>
            <span className="rounded border border-line bg-surface-2 px-1.5 py-0.5 font-medium">
              15 export files
            </span>
            <span className="rounded border border-line bg-surface-2 px-1.5 py-0.5 font-medium">
              7 formats
            </span>
          </p>
        </div>

        {activeTab === 'search' && (
          <div className="mt-7 grid grid-cols-1 items-start gap-5 lg:grid-cols-12">
            {/* Query + assistant column */}
            <div className="space-y-5 lg:col-span-5 xl:col-span-4">
              <SearchForm onSearch={handleSearch} isRunning={isRunning} />
              <AIAssistant
                searchId={search && search.search_id !== 'error' ? search.search_id : null}
                query={search?.query ?? null}
              />
            </div>

            {/* Output column */}
            <div className="space-y-5 lg:col-span-7 xl:col-span-8">
              {hasResults && <ResultsView search={search!} />}
              {search ? (
                <ProgressView search={search} logLines={logLines} />
              ) : (
                <div className="panel px-5 py-8 sm:px-7 sm:py-10">
                  <h2 className="text-base font-semibold text-fg">
                    Ready when you are
                  </h2>
                  <p className="mt-1.5 max-w-prose text-sm leading-relaxed text-fg-muted">
                    Enter a protein or compound on the left and run a search. The
                    engine expands the query across every connected database and
                    streams its progress here, then lists the files you can
                    download.
                  </p>
                  <p className="mt-5 text-xs text-fg-subtle">
                    Searches run in parallel. If the engine is already at its
                    concurrency limit, yours is queued and starts automatically.
                  </p>
                </div>
              )}
            </div>
          </div>
        )}

        {activeTab === 'history' && (
          <div className="mt-7">
            <HistoryPanel onViewSearch={handleViewSearch} />
          </div>
        )}
      </main>

      <SiteFooter />
    </div>
  );
}
