'use client';

/**
 * Landing route for Supabase's password-reset email.
 *
 * Supabase puts a recovery session in the URL when the user returns; the form
 * below then calls supabase.auth.updateUser to set the new password. Nothing
 * about the session is handled here.
 */

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { SiteFooter, SiteHeader } from '@/components/SiteChrome';
import { AlertIcon, CheckIcon, SpinnerIcon } from '@/components/icons';
import { btnPrimary, btnSecondary, note } from '@/components/ui';
import { authErrorMessage, getSupabase } from '@/lib/supabase';
import PasswordField from '@/components/PasswordField';

export default function ResetPasswordPage() {
  const [ready, setReady] = useState(false);
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
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
    if (password !== confirm) {
      setError('The two passwords do not match.');
      return;
    }
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
    <div className="flex min-h-screen flex-col">
      <SiteHeader current="app" />

      <main className="flex flex-1 items-center justify-center px-4 py-12">
        <div className="w-full max-w-sm">
          <h1 className="text-xl font-semibold tracking-tight text-fg">
            Choose a new password
          </h1>

          {done ? (
            <div className="panel mt-4 p-5">
              <p className="flex items-start gap-2 text-sm leading-relaxed text-fg">
                <CheckIcon className="mt-0.5 h-4 w-4 shrink-0 text-success" />
                Your password has been updated. You can sign in with it now.
              </p>
              <Link href="/" className={`${btnPrimary} mt-4 w-full`}>
                Back to the app
              </Link>
            </div>
          ) : !ready ? (
            <p
              className="mt-4 flex items-center gap-2 text-sm text-fg-muted"
              role="status"
            >
              <SpinnerIcon className="h-4 w-4 animate-spin-slow" />
              Checking your reset link…
            </p>
          ) : (
            <form onSubmit={submit} className="panel mt-4 space-y-4 p-5">
              <p className="text-sm leading-relaxed text-fg-muted">
                Open this page from the link in your password-reset email. The
                link signs you in just long enough to set a new password.
              </p>

              <PasswordField
                id="new-password"
                label="New password"
                value={password}
                onChange={setPassword}
                autoComplete="new-password"
                required
                minLength={8}
                placeholder="At least 8 characters"
                hint="At least 8 characters."
              />

              <PasswordField
                id="confirm-password"
                label="Confirm new password"
                value={confirm}
                onChange={setConfirm}
                autoComplete="new-password"
                required
                minLength={8}
                placeholder="Re-enter your new password"
                hint="Re-enter your new password to confirm it."
              />
              {password && confirm && password !== confirm && (
                <p className={note('danger')} role="alert">
                  <span className="inline-flex items-start gap-1.5">
                    <AlertIcon className="mt-px h-3.5 w-3.5 shrink-0" />
                    The two passwords do not match.
                  </span>
                </p>
              )}

              {error && (
                <p className={note('danger')} role="alert">
                  <span className="inline-flex items-start gap-1.5">
                    <AlertIcon className="mt-px h-3.5 w-3.5 shrink-0" />
                    {error}
                  </span>
                </p>
              )}

              <button type="submit" disabled={busy} className={`${btnPrimary} w-full`}>
                {busy ? 'Saving…' : 'Set new password'}
              </button>

              <Link href="/" className={`${btnSecondary} w-full`}>
                Cancel
              </Link>
            </form>
          )}
        </div>
      </main>

      <SiteFooter />
    </div>
  );
}
