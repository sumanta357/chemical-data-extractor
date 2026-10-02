'use client';

/**
 * Landing route for Supabase's password-reset email.
 *
 * Supabase puts a recovery session in the URL when the user returns; the form
 * below then calls supabase.auth.updateUser to set the new password. Nothing
 * about the session is handled here.
 */

import { useEffect, useState } from 'react';
import { authErrorMessage, getSupabase } from '@/lib/supabase';

export default function ResetPasswordPage() {
  const [ready, setReady] = useState(false);
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const supabase = getSupabase();
      if (!supabase) {
        // The client is configured from the API, so fall back to the config
        // endpoint before deciding a recovery session cannot exist.
        const res = await fetch('/api/billing/config');
        const cfg = await res.json();
        getSupabase(cfg?.supabase?.url, cfg?.supabase?.anon_key);
      }
      if (!cancelled) setReady(true);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    const supabase = getSupabase();
    if (!supabase) return;
    setBusy(true);
    setError(null);
    try {
      const { error: updateError } = await supabase.auth.updateUser({ password });
      if (updateError) throw updateError;
      setDone(true);
    } catch (err: any) {
      setError(authErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="min-h-screen bg-[#050912] text-gray-300 px-4 py-16">
      <div className="max-w-sm mx-auto space-y-4">
        <h1 className="text-lg font-semibold text-gray-100">Choose a new password</h1>

        {done ? (
          <>
            <p className="text-sm text-cyan-400">Your password has been updated.</p>
            <a
              href="/"
              className="inline-block text-sm px-4 py-2 border border-cyan-600 text-cyan-400 hover:bg-cyan-500/10"
            >
              Back to the app
            </a>
          </>
        ) : !ready ? (
          <p className="text-sm text-gray-500">Loading…</p>
        ) : (
          <form onSubmit={submit} className="space-y-3">
            <p className="text-sm text-gray-500">
              Open this page from the link in your password-reset email.
            </p>
            <input
              type="password"
              required
              minLength={8}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="New password"
              className="w-full bg-transparent border border-gray-800 px-3 py-2 text-sm text-gray-200 focus:border-cyan-600 focus:outline-none"
            />
            {error && <p className="text-xs text-amber-400">{error}</p>}
            <button
              type="submit"
              disabled={busy}
              className="w-full text-sm font-medium py-2 border border-cyan-600 text-cyan-400 hover:bg-cyan-500/10 disabled:opacity-50"
            >
              {busy ? 'Saving…' : 'Set new password'}
            </button>
          </form>
        )}
      </div>
    </main>
  );
}
