'use client';

/**
 * Landing route for Supabase's "Confirm email" mail.
 *
 * Before this existed the confirmation link pointed at `/`, so a new user was
 * dropped on the landing page with no sign the click had done anything. They
 * had to guess whether they were verified and then try to sign in.
 *
 * Supabase hands the result back in the URL. The browser client is built with
 * `detectSessionInUrl`, so the PKCE token in that link establishes a session on
 * its own: opening the mail link *is* the first sign-in. Nothing here parses a
 * token, and nothing here decides that an address is verified -- the session
 * Supabase hands back is the only evidence, which is why an expired or already
 * used link lands on the error path instead of a false "confirmed".
 */

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { SiteFooter, SiteHeader } from '@/components/SiteChrome';
import { AlertIcon, CheckIcon, SpinnerIcon } from '@/components/icons';
import { btnPrimary, btnSecondary, note } from '@/components/ui';
import { getSupabase } from '@/lib/supabase';

type Phase = 'checking' | 'confirmed' | 'already' | 'error';

export default function ConfirmEmailPage() {
  const [phase, setPhase] = useState<Phase>('checking');
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      // The client is configured from /api/billing/config rather than baked
      // into the bundle, so a cold load has none yet — same bootstrap the
      // password-reset page uses.
      let supabase = getSupabase();
      if (!supabase) {
        try {
          const res = await fetch('/api/billing/config');
          const cfg = await res.json();
          supabase = getSupabase(cfg?.supabase?.url, cfg?.supabase?.anon_key);
        } catch {
          supabase = null;
        }
      }
      if (cancelled) return;

      if (!supabase) {
        setPhase('error');
        setMessage(
          'Sign-in is not configured on this server, so this link cannot be checked.',
        );
        return;
      }

      // The client needs a moment to exchange the token in the URL for a
      // session; on an older PKCE link that exchange is asynchronous.
      let session = null;
      try {
        const { data } = await supabase.auth.getSession();
        session = data.session;
      } catch {
        session = null;
      }

      if (cancelled) return;

      if (session?.user) {
        // Distinguish a genuinely new confirmation from a link opened twice.
        // Supabase records when the address was confirmed; a link opened after
        // that point is a repeat visit, not a new verification.
        const confirmedAt =
          session.user.email_confirmed_at ?? session.user.confirmed_at ?? null;
        const confirmedRecently =
          confirmedAt && Date.now() - new Date(confirmedAt).getTime() < 10 * 60_000;
        setPhase(confirmedRecently ? 'confirmed' : 'already');
        return;
      }

      setPhase('error');
      setMessage(
        'This confirmation link is no longer valid. It may already have been used, or it may have expired.',
      );
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  const heading: Record<Phase, string> = {
    checking: 'Confirming your email',
    confirmed: 'Email verification confirmed',
    already: 'Your email is already confirmed',
    error: 'This link could not be confirmed',
  };

  const body: Record<Phase, string> = {
    checking: 'Checking your confirmation link…',
    confirmed:
      'Your email address is verified and you are signed in. This link signed you in for the first time, so you do not need to enter your password again here.',
    already:
      'This email address was verified earlier and you are signed in. Nothing more is needed.',
    error: '',
  };

  return (
    <div className="flex min-h-screen flex-col">
      <SiteHeader current="app" />

      <main className="flex flex-1 items-center justify-center px-4 py-12">
        <div className="w-full max-w-sm">
          <h1 className="text-xl font-semibold tracking-tight text-fg">
            {heading[phase]}
          </h1>

          {phase === 'checking' ? (
            <p
              className="mt-4 flex items-center gap-2 text-sm text-fg-muted"
              role="status"
            >
              <SpinnerIcon className="h-4 w-4 animate-spin-slow" />
              {body.checking}
            </p>
          ) : (
            <div className="panel mt-4 p-5">
              {phase === 'error' ? (
                <p className={note('danger')} role="alert">
                  <span className="inline-flex items-start gap-1.5">
                    <AlertIcon className="mt-px h-3.5 w-3.5 shrink-0" />
                    {message}
                  </span>
                </p>
              ) : (
                <p className="flex items-start gap-2 text-sm leading-relaxed text-fg">
                  <CheckIcon className="mt-0.5 h-4 w-4 shrink-0 text-success" />
                  {body[phase]}
                </p>
              )}

              {phase === 'error' ? (
                <>
                  <Link href="/" className={`${btnPrimary} mt-4 w-full`}>
                    Go to sign in
                  </Link>
                  <p className="mt-3 text-xs leading-relaxed text-fg-subtle">
                    Signing in with the same email address confirms it as well,
                    so a used link is not a dead end.
                  </p>
                </>
              ) : (
                <>
                  <Link href="/" className={`${btnPrimary} mt-4 w-full`}>
                    Start searching
                  </Link>
                  <p className="mt-3 text-xs leading-relaxed text-fg-subtle">
                    You are signed in on this device only. Use your email and
                    password to sign in anywhere else.
                  </p>
                </>
              )}

              <Link href="/reset-password" className={`${btnSecondary} mt-2 w-full`}>
                Forgot your password?
              </Link>
            </div>
          )}
        </div>
      </main>

      <SiteFooter />
    </div>
  );
}