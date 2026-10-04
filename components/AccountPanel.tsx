'use client';

import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { createPortal } from 'react-dom';
import {
  AcademicProfile,
  BillingConfig,
  Entitlement,
  Plan,
  createOrder,
  getConfig,
  getMe,
  getPlans,
  getProfile,
  loadCheckoutScript,
  paymentStatus,
  updateProfile,
  verifyPayment,
} from '@/lib/billing';
import { authErrorMessage, getSupabase } from '@/lib/supabase';
import {
  getAcademicStatus,
  verifyAcademic,
  type AcademicVerification,
} from '@/lib/billing';
import {
  AlertIcon,
  ArrowRightIcon,
  CheckIcon,
  CloseIcon,
  SpinnerIcon,
  UserIcon,
} from '@/components/icons';
import {
  btnGhost,
  btnPrimary,
  btnSecondary,
  cx,
  note,
  segmentItemClass,
  segmentWrap,
} from '@/components/ui';

type View = 'account' | 'plans' | 'academic';
type AuthView = 'form' | 'forgot';

const ACADEMICIAN_TYPES = [
  'Professor',
  'Associate Professor',
  'Assistant Professor',
  'Research Scientist',
  'Research Scholar',
  'PhD Scholar',
  'Postdoctoral Researcher',
  'Lecturer',
  'Other',
];

const EMPTY_PROFILE: AcademicProfile = {
  academic_email: '',
  institute_name: '',
  institute_id: '',
  phone: '',
  academician_type: '',
};

function GoogleMark() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">
      <path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92a5.06 5.06 0 0 1-2.2 3.32v2.77h3.57c2.08-1.92 3.27-4.74 3.27-8.1Z" />
      <path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84A11 11 0 0 0 12 23Z" />
      <path fill="#FBBC05" d="M5.84 14.1a6.6 6.6 0 0 1 0-4.2V7.06H2.18a11 11 0 0 0 0 9.88l3.66-2.84Z" />
      <path fill="#EA4335" d="M12 4.75c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 1.46 14.97.5 12 .5A11 11 0 0 0 2.18 7.06l3.66 2.84C6.71 6.68 9.14 4.75 12 4.75Z" />
    </svg>
  );
}

type CheckoutState =
  | { phase: 'idle' }
  | { phase: 'preparing' }
  | { phase: 'awaiting' }
  | { phase: 'verifying' }
  | { phase: 'done'; message: string }
  | { phase: 'cancelled' }
  | { phase: 'error'; message: string };

interface Props {
  open: boolean;
  onOpen: () => void;
  onClose: () => void;
  onChange?: (entitlement: Entitlement) => void;
  /** Render the panel as soon as it mounts — used by the standalone
   *  /reset-password page. */
  initiallyOpen?: boolean;
  /** Hide the header trigger; only for those standalone pages. */
  hideTrigger?: boolean;
}

export default function AccountPanel({
  open,
  onOpen,
  onClose,
  onChange,
  initiallyOpen = false,
  hideTrigger = false,
}: Props) {
  const [config, setConfig] = useState<BillingConfig | null>(null);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [entitlement, setEntitlement] = useState<Entitlement | null>(null);
  const [view, setView] = useState<View>('account');
  const [email, setEmail] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [opened, setOpened] = useState(false);
  const [password, setPassword] = useState('');
  const [mode, setMode] = useState<'signin' | 'signup'>('signin');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [authView, setAuthView] = useState<AuthView>('form');
  const [profile, setProfile] = useState<AcademicProfile>(EMPTY_PROFILE);
  const [profileMsg, setProfileMsg] = useState<string | null>(null);
  const [profileErr, setProfileErr] = useState<string | null>(null);
  const [checkout, setCheckout] = useState<CheckoutState>({ phase: 'idle' });
  const [academic, setAcademic] = useState<AcademicVerification | null>(null);
  const [academicToken, setAcademicToken] = useState<string | null>(null);
  const [academicBusy, setAcademicBusy] = useState(false);
  const [academicError, setAcademicError] = useState<string | null>(null);
  const [academicNotice, setAcademicNotice] = useState<string | null>(null);
  const [mounted, setMounted] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  // The host page owns `open`; the emailed-link pages own `opened`.
  const isPanelOpen = hideTrigger ? opened : open;

  // ── Supabase session ─────────────────────────────────────────────────────
  // The client is created from the server config, and we follow Supabase's
  // session: sign-in, OAuth redirect and token refresh all happen there. The
  // server independently verifies the access token we send.
  const supabase = useMemo(
    () => getSupabase(config?.supabase?.url, config?.supabase?.anon_key),
    [config?.supabase?.url, config?.supabase?.anon_key],
  );

  const loadProfile = useCallback(async () => {
    try {
      const { profile: p } = await getProfile();
      setProfile({ ...EMPTY_PROFILE, ...p });
    } catch {
      /* the profile is optional; ignore */
    }
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [me, cfg] = await Promise.all([
        getMe(),
        config ? Promise.resolve(config) : getConfig(),
      ]);
      setEntitlement(me.entitlement);
      setConfig(cfg);
      onChange?.(me.entitlement);
      if (me.entitlement.signed_in) void loadProfile();
      void getAcademicStatus()
        .then((r) => setAcademic(r.verification))
        .catch(() => {});
    } catch {
      /* stay anonymous if the account service is unavailable */
    }
  }, [config, onChange, loadProfile]);

  // Supabase owns sign-out; there is no server session to destroy.
  useEffect(() => {
    if (!supabase) return;
    let active = true;
    const { data } = supabase.auth.onAuthStateChange((event) => {
      if (!active) return;
      if (event === 'SIGNED_IN' || event === 'TOKEN_REFRESHED' || event === 'SIGNED_OUT') {
        void refresh();
      }
    });
    return () => {
      active = false;
      data.subscription.unsubscribe();
    };
  }, [supabase, refresh]);

  // The password-reset link from Supabase lands here with a recovery session;
  // just open the panel so the user can choose a new password.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    if (!window.location.pathname.replace(/\/$/, '').endsWith('/reset-password')) return;
    setView('account');
    onOpen();
  }, [onOpen]);

  useEffect(() => {
    void refresh();
    getPlans().then((r) => setPlans(r.plans)).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The panel renders into a portal on <body>, which only exists in the
  // browser. This flag gates that portal so the server-rendered HTML matches
  // the first client render; without it set to true the modal is never
  // created and clicking the trigger looks completely dead.
  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (initiallyOpen && !opened) {
      setOpened(true);
      onOpen();
    }
  }, [initiallyOpen, opened, onOpen]);

  useEffect(() => {
    if (open) {
      setError(null);
      setCheckout({ phase: 'idle' });
      void refresh();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Dialog behaviour: Escape closes, the page behind cannot scroll, and focus
  // moves into the dialog so a keyboard user is never left on the trigger.
  // The effect is keyed on the open state only — `onClose` is a fresh closure
  // on every parent render, and depending on it would re-run this effect (and
  // pull focus out of whatever field is being typed into) once per poll tick.
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });
  useEffect(() => {
    if (!isPanelOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCloseRef.current();
    };
    document.addEventListener('keydown', onKeyDown);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    dialogRef.current?.focus();
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.body.style.overflow = previousOverflow;
    };
  }, [isPanelOpen]);

  async function submitAuth(event: React.FormEvent) {
    event.preventDefault();
    if (!supabase) {
      setError('Sign-in is not configured on this server yet.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      if (mode === 'signin') {
        const { error: authError } = await supabase.auth.signInWithPassword({ email, password });
        if (authError) throw authError;
        setPassword('');
      } else {
        const { data, error: authError } = await supabase.auth.signUp({
          email,
          password,
          options: {
            emailRedirectTo: `${window.location.origin}/`,
            data: { full_name: displayName || undefined },
          },
        });
        if (authError) throw authError;
        // With "Confirm email" on, Supabase returns a user but no session and
        // sends the confirmation mail instead.
        setNotice(
          data.session
            ? 'Account created. Welcome aboard.'
            : 'Check your inbox to confirm your email address, then sign in.',
        );
        setMode('signin');
      }
      setView('account');
    } catch (err: any) {
      setError(authErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function signInWithGoogle() {
    if (!supabase) {
      setError('Sign-in is not configured on this server yet.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const { error: authError } = await supabase.auth.signInWithOAuth({
        provider: 'google',
        options: { redirectTo: `${window.location.origin}/` },
      });
      // No error means the browser is being redirected to Google.
      if (authError) throw authError;
    } catch (err: any) {
      setError(authErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function submitForgot(event: React.FormEvent) {
    event.preventDefault();
    if (!supabase) {
      setError('Sign-in is not configured on this server yet.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const { error: authError } = await supabase.auth.resetPasswordForEmail(email, {
        // Supabase sends the link here; the recovery session is picked up by
        // the client automatically when the user returns.
        redirectTo: `${window.location.origin}/reset-password`,
      });
      // Always the same answer, so this cannot be used to discover accounts.
      setNotice(
        authError
          ? 'If that address has an account, a reset link is on its way.'
          : 'If that address has an account, a reset link is on its way.',
      );
    } finally {
      setBusy(false);
    }
  }

  async function saveProfile(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setProfileMsg(null);
    setProfileErr(null);
    try {
      const { profile: saved } = await updateProfile(profile);
      setProfile({ ...EMPTY_PROFILE, ...saved });
      setProfileMsg('Profile saved.');
    } catch (err: any) {
      setProfileErr(err.message || 'Could not save your profile');
    } finally {
      setBusy(false);
    }
  }

  async function signOut() {
    await supabase?.auth.signOut().catch(() => {});
    setEntitlement(null);
    onChange?.({
      signed_in: false,
      plan: 'free',
      plan_name: 'Guest',
      searches_limit: 0,
      searches_used: 0,
      searches_remaining: 0,
      analyst_limit: 0,
      analyst_used: 0,
      analyst_remaining: 0,
      billing_enabled: config?.billing_enabled ?? false,
      auth_required: config?.auth_required ?? false,
      status: 'anonymous',
    });
  }

  async function runAcademicVerification(e?: FormEvent, emailOverride?: string) {
    e?.preventDefault();
    const email = (emailOverride ?? profile.academic_email ?? '').trim();
    if (!email) {
      setAcademicError('Enter your institutional email address.');
      return;
    }
    setAcademicBusy(true);
    setAcademicError(null);
    setAcademicNotice(null);
    try {
      const result = await verifyAcademic({
        academic_email: email,
        institute_name: profile.institute_name || undefined,
        institute_id: profile.institute_id || undefined,
        academician_type: profile.academician_type || undefined,
      });
      setAcademic(result.verification);
      setAcademicToken(result.token);
      setProfile((p) => ({ ...p, academic_email: email }));
      setCheckout({ phase: 'idle' });
      setAcademicNotice(result.message);
    } catch (err: any) {
      // A refused address must not leave a stale token behind, or the user
      // would reach a payment screen the server will reject anyway.
      setAcademicToken(null);
      setAcademicError(err?.message || 'Could not verify that academic email.');
    } finally {
      setAcademicBusy(false);
    }
  }

  /** Enter the verification step, pre-filling the account address and checking
   *  it straight away so a recognised institutional domain is approved without
   *  the visitor typing anything. */
  function beginAcademicVerification() {
    setCheckout({ phase: 'idle' });
    setAcademicError(null);
    setAcademicNotice(null);
    setView('academic');
    const accountEmail = entitlement?.email || '';
    if (!profile.academic_email && accountEmail) {
      setProfile((p) => ({ ...p, academic_email: accountEmail }));
    }
    if (academic?.status !== 'verified' && accountEmail) {
      void runAcademicVerification(undefined, accountEmail);
    }
  }

  async function startCheckout(plan: Plan, academicToken?: string | null) {
    setCheckout({ phase: 'preparing' });
    setError(null);
    if (!signedIn) {
      // Purchasing needs an account — send them to sign in first.
      setCheckout({ phase: 'idle' });
      setView('account');
      setError('Create an account or sign in to continue with your purchase.');
      return;
    }
    try {
      if (!config?.key_id) throw new Error('Payments are not configured on this server yet.');
      const order = await createOrder(plan.id, academicToken);
      const loaded = await loadCheckoutScript(config.checkout_script);
      if (!loaded) throw new Error('Could not load the secure checkout.');

      await new Promise<void>((resolve, reject) => {
        const Razorpay = (window as any).Razorpay;
        if (!Razorpay) return reject(new Error('Checkout is unavailable in this browser.'));

        const instance = new Razorpay({
          key: order.key_id,
          order_id: order.order_id,
          amount: order.amount,
          currency: order.currency,
          name: 'Chemical Data Extractor',
          description: `${plan.name} plan — ${plan.searches} searches per month`,
          handler: async (response: any) => {
            try {
              setCheckout({ phase: 'verifying' });
              const result = await verifyPayment({
                razorpay_order_id: response.razorpay_order_id,
                razorpay_payment_id: response.razorpay_payment_id,
                razorpay_signature: response.razorpay_signature,
              });
              setEntitlement(result.entitlement);
              onChange?.(result.entitlement);

              // Confirm with the server what it recorded for this payment.
              const status = await paymentStatus(response.razorpay_payment_id).catch(() => null);
              setCheckout({
                phase: 'done',
                message: status
                  ? `${result.message} Payment ${response.razorpay_payment_id.slice(0, 12)}… recorded as ${status.status}.`
                  : result.message,
              });
            } catch (err: any) {
              // The checkout signature did not verify. Before giving up, ask the
              // server to reconcile with Razorpay — it can still credit the plan
              // if the payment really was captured.
              try {
                const status = await paymentStatus(response.razorpay_payment_id);
                if (status.entitlement?.signed_in && status.entitlement.plan !== 'free') {
                  setEntitlement(status.entitlement);
                  onChange?.(status.entitlement);
                  setCheckout({ phase: 'done', message: `${status.entitlement.plan_name} plan is active.` });
                  return;
                }
              } catch {
                /* fall through to the error message */
              }
              setCheckout({
                phase: 'error',
                message:
                  err.message ||
                  'We could not verify this payment. If you were charged, contact support with your payment id.',
              });
            }
            resolve();
          },
          modal: {
            ondismiss: () => {
              setCheckout({ phase: 'cancelled' });
              resolve();
            },
          },
          theme: { color: '#0c6a80' },
        });
        setCheckout({ phase: 'awaiting' });
        instance.open();
      });
    } catch (err: any) {
      setCheckout({ phase: 'error', message: err.message || 'Could not start the payment.' });
    }
  }

  const signedIn = entitlement?.signed_in ?? false;
  const academicVerified = academic?.status === 'verified';
  const used = entitlement?.searches_used ?? 0;
  const limit = entitlement?.searches_limit ?? 0;
  // Only offer Google when the Supabase project has it switched on. Clicking a
  // disabled provider fails with "Unsupported provider: provider is not
  // enabled", which reads as a broken login rather than a disabled option.
  const googleEnabled = config?.supabase?.providers?.google === true;
  // Any in-flight checkout phase locks every plan card, so a second click can
  // never open a duplicate Razorpay modal.
  const checkoutBusy =
    checkout.phase === 'preparing' ||
    checkout.phase === 'awaiting' ||
    checkout.phase === 'verifying';

  const openAt = (next: View) => {
    setError(null);
    setCheckout({ phase: 'idle' });
    setView(next);
    onOpen();
  };

  const busyLabel =
    checkout.phase === 'preparing'
      ? 'Preparing secure checkout…'
      : checkout.phase === 'awaiting'
      ? 'Waiting for payment…'
      : checkout.phase === 'verifying'
      ? 'Verifying payment…'
      : null;

  /* Signing in and buying are separate intentions. One button labelled
     "Pricing" that opened the plan grid left a visitor who only wanted to
     sign in guessing at checkout, so each now gets its own control. */
  const trigger = hideTrigger ? null : signedIn ? (
    <button
      onClick={() => openAt('account')}
      className={cx(btnSecondary, 'shrink-0 whitespace-nowrap')}
      aria-label={`Account: ${entitlement?.plan_name}, ${used} of ${limit || 'unlimited'} searches used`}
    >
      <UserIcon className="h-4 w-4" />
      <span className="tabular-nums">
        {entitlement?.plan_name} · {used}/{limit || '—'}
      </span>
    </button>
  ) : (
    <>
      <button onClick={() => openAt('account')} className={cx(btnSecondary, 'shrink-0')}>
        Sign in
      </button>
      <button onClick={() => openAt('plans')} className={cx(btnGhost, 'hidden shrink-0 sm:inline-flex')}>
        Pricing
      </button>
    </>
  );

  const titles: Record<View, string> = {
    account: 'Your account',
    plans: 'Plans and pricing',
    academic: 'Academic verification',
  };

  const subtitles: Record<View, string> = {
    account: signedIn
      ? entitlement?.email ?? ''
      : 'Create a free account to run searches and track usage.',
    plans: 'Pay securely by card, UPI or netbanking.',
    academic: 'Confirm your institutional email to unlock the Academic plan.',
  };

  const modal = isPanelOpen ? (
    <div
      className="fixed inset-0 z-[100] flex items-start justify-center overflow-y-auto bg-overlay/70 px-4 py-6 sm:py-12"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={titles[view]}
        tabIndex={-1}
        className="panel w-full max-w-2xl overflow-hidden shadow-overlay outline-none"
      >
        <div className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              {view !== 'account' && (
                <button
                  type="button"
                  onClick={() => openAt('account')}
                  className="-ml-1.5 rounded p-1 text-fg-subtle transition-colors hover:bg-surface-2 hover:text-fg"
                  aria-label="Back to your account"
                >
                  <ArrowRightIcon className="h-4 w-4 rotate-180" />
                </button>
              )}
              <h2 className="text-base font-semibold text-fg">{titles[view]}</h2>
            </div>
            <p className="mt-0.5 truncate text-xs text-fg-subtle">{subtitles[view]}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="-mr-1.5 -mt-1 shrink-0 rounded-md p-1.5 text-fg-subtle transition-colors hover:bg-surface-2 hover:text-fg"
          >
            <CloseIcon className="h-4 w-4" />
          </button>
        </div>

        <div className="max-h-[70vh] overflow-y-auto px-5 py-5">
          {notice && <p className={cx(note('info'), 'mb-4')}>{notice}</p>}

          {view === 'plans' && (
            <div className="space-y-4">
              {busyLabel && (
                <p className="flex items-center gap-2" role="status">
                  <SpinnerIcon className="h-3.5 w-3.5 animate-spin-slow text-accent" />
                  <span className="text-xs text-fg-muted">{busyLabel}</span>
                </p>
              )}
              {checkout.phase === 'done' && (
                <p className={note('success')} role="status">
                  <span className="inline-flex items-center gap-1.5">
                    <CheckIcon className="h-3.5 w-3.5" />
                    {checkout.message}
                  </span>
                </p>
              )}
              {checkout.phase === 'cancelled' && (
                <p className={note('info')}>Payment cancelled. Nothing was charged.</p>
              )}
              {checkout.phase === 'error' && (
                <p className={note('danger')} role="alert">
                  <span className="inline-flex items-start gap-1.5">
                    <AlertIcon className="mt-px h-3.5 w-3.5 shrink-0" />
                    {checkout.message}
                  </span>
                </p>
              )}

              {/* Reaching the plan grid signed out used to be a dead end:
                  nothing there said how to actually buy anything. */}
              {!signedIn && (
                <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-accent-line bg-accent-soft px-3 py-2.5">
                  <p className="text-xs text-fg">
                    Sign in or create a free account to buy a plan.
                  </p>
                  <button
                    type="button"
                    onClick={() => openAt('account')}
                    className={cx(btnPrimary, 'shrink-0 px-3 py-1.5 text-xs')}
                  >
                    Sign in
                  </button>
                </div>
              )}

              {plans.filter((p) => p.price_inr > 0).length === 0 && (
                <p className={note('warning')}>
                  Plans could not be loaded. Please close this and try again.
                </p>
              )}

              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                {plans
                  .filter((p) => p.price_inr > 0)
                  .map((plan) => (
                    /* The whole card is the button: people click the price or the
                       feature list, not just the small label at the bottom. */
                    <button
                      key={plan.id}
                      type="button"
                      onClick={() => {
                        if (!signedIn) {
                          // Verification is tied to an account, so send signed-out
                          // visitors to sign in rather than to a view that would
                          // have nothing to show them.
                          setCheckout({ phase: 'idle' });
                          setView('account');
                          setError('Create an account or sign in to verify an academic email.');
                          return;
                        }
                        if (plan.id === 'academic' && !academicVerified) {
                          // Academic must be verified server-side before money
                          // moves; show the verification step instead of checkout.
                          beginAcademicVerification();
                          return;
                        }
                        startCheckout(plan, plan.id === 'academic' ? academicToken : null);
                      }}
                      disabled={checkoutBusy}
                      aria-busy={checkout.phase === 'preparing'}
                      className="flex flex-col rounded-lg border border-line bg-surface-2 p-4 text-left transition-colors hover:border-accent-line hover:bg-accent-soft disabled:cursor-not-allowed disabled:opacity-55"
                    >
                      <span className="flex items-baseline justify-between gap-2">
                        <span className="text-sm font-semibold text-fg">{plan.name}</span>
                        <span className="whitespace-nowrap text-sm tabular-nums text-fg">
                          ₹{plan.price_inr.toLocaleString('en-IN')}
                          <span className="text-xs font-normal text-fg-subtle">/mo</span>
                        </span>
                      </span>
                      {plan.tag && (
                        <span className="mt-1 block text-xs text-fg-subtle">{plan.tag}</span>
                      )}
                      <ul className="mt-3 flex-1 space-y-1">
                        {plan.features.map((f) => (
                          <li key={f} className="flex gap-1.5 text-xs text-fg-muted">
                            <CheckIcon className="mt-px h-3.5 w-3.5 shrink-0 text-accent" />
                            {f}
                          </li>
                        ))}
                      </ul>
                      <span
                        className={cx(
                          'mt-4 block w-full rounded-md border py-2 text-center text-sm font-medium',
                          checkoutBusy
                            ? 'border-line text-fg-subtle'
                            : 'border-accent text-accent'
                        )}
                      >
                        {checkoutBusy
                          ? 'Opening checkout…'
                          : `Pay ₹${plan.price_inr.toLocaleString('en-IN')}`}
                      </span>
                    </button>
                  ))}
              </div>

              {error && <p className={note('warning')}>{error}</p>}
            </div>
          )}

          {view === 'account' && !signedIn && authView === 'forgot' && (
            <form onSubmit={submitForgot} className="max-w-sm space-y-4">
              <p className="text-sm leading-relaxed text-fg-muted">
                Enter your email and we will send you a link to choose a new password.
              </p>
              <div>
                <label htmlFor="forgot-email" className="label">
                  Email
                </label>
                <input
                  id="forgot-email"
                  type="email"
                  autoComplete="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="field"
                />
              </div>
              {error && <p className={note('warning')}>{error}</p>}
              <button type="submit" disabled={busy} className={cx(btnPrimary, 'w-full')}>
                {busy ? 'Sending…' : 'Send reset link'}
              </button>
              <button
                type="button"
                onClick={() => {
                  setAuthView('form');
                  setError(null);
                }}
                className="text-xs text-fg-muted transition-colors hover:text-fg"
              >
                Back to sign in
              </button>
            </form>
          )}

          {view === 'academic' && !signedIn && (
            <div className="max-w-sm space-y-4">
              <p className="text-sm leading-relaxed text-fg-muted">
                Academic eligibility is tied to your account. Sign in (or create a
                free account) and we will check your institutional email before
                you pay.
              </p>
              <button
                type="button"
                onClick={() => setView('account')}
                className={cx(btnPrimary, 'w-full')}
              >
                Sign in or create an account
              </button>
            </div>
          )}

          {view === 'academic' && signedIn && (
            <form onSubmit={runAcademicVerification} className="max-w-sm space-y-4">
              <p className="text-sm leading-relaxed text-fg-muted">
                The Academic plan is for students and academic labs. Enter your
                institutional email and we will check it against the recognised
                academic domains before you pay.
              </p>

              <div>
                <label htmlFor="academic-email" className="label">
                  Academic email
                </label>
                <input
                  id="academic-email"
                  type="email"
                  autoComplete="email"
                  required
                  value={profile.academic_email || ''}
                  onChange={(e) =>
                    setProfile((p) => ({ ...p, academic_email: e.target.value }))
                  }
                  placeholder="you@university.edu"
                  className="field"
                />
              </div>

              <div>
                <label htmlFor="academic-institute" className="label">
                  Institution
                </label>
                <input
                  id="academic-institute"
                  type="text"
                  value={profile.institute_name || ''}
                  onChange={(e) =>
                    setProfile((p) => ({ ...p, institute_name: e.target.value }))
                  }
                  className="field"
                />
              </div>

              <div>
                <label htmlFor="academic-role" className="label">
                  Your role
                </label>
                <select
                  id="academic-role"
                  value={profile.academician_type || ''}
                  onChange={(e) =>
                    setProfile((p) => ({ ...p, academician_type: e.target.value }))
                  }
                  className="field"
                >
                  <option value="">Select…</option>
                  {ACADEMICIAN_TYPES.map((t) => (
                    <option key={t} value={t}>
                      {t}
                    </option>
                  ))}
                </select>
              </div>

              {academicNotice && <p className={note('success')}>{academicNotice}</p>}
              {academicError && (
                <p className={note('danger')} role="alert">
                  {academicError}
                </p>
              )}

              <button type="submit" disabled={academicBusy} className={cx(btnPrimary, 'w-full')}>
                {academicBusy ? 'Checking…' : 'Verify academic email'}
              </button>

              {academicVerified && (
                <button
                  type="button"
                  onClick={() => {
                    const plan = plans.find((p) => p.id === 'academic');
                    if (plan) startCheckout(plan, academicToken);
                  }}
                  disabled={checkoutBusy || !academicToken}
                  className={cx(btnSecondary, 'w-full')}
                >
                  Continue to payment — ₹249
                </button>
              )}

              <p className="text-[11px] leading-relaxed text-fg-subtle">
                {academicVerified
                  ? `Verified as ${academic?.verified_email}. The verification is checked again on the server when the payment is made.`
                  : academic?.mailbox_proof_required
                  ? 'We will email a confirmation link to the address you enter.'
                  : 'We currently confirm that the domain belongs to an academic institution.'}
              </p>
            </form>
          )}

          {view === 'account' && !signedIn && authView === 'form' && (
            <form onSubmit={submitAuth} className="max-w-sm space-y-4">
              {config?.auth_required && (
                <p className={note('info')}>
                  {mode === 'signup'
                    ? 'Create a free account to start searching — it takes a few seconds and your 3 free searches are waiting.'
                    : 'Sign in to search. New here? Choose “Create account” below for 3 free searches.'}
                </p>
              )}

              {mode === 'signin' && supabase && googleEnabled && (
                <div className="pb-1">
                  <button
                    type="button"
                    onClick={signInWithGoogle}
                    disabled={busy}
                    className={cx(btnSecondary, 'w-full gap-2.5')}
                  >
                    <GoogleMark />
                    Continue with Google
                  </button>
                  <div className="my-4 flex items-center gap-3">
                    <span aria-hidden className="h-px flex-1 bg-line" />
                    <span className="text-[10px] uppercase tracking-widest text-fg-subtle">
                      or
                    </span>
                    <span aria-hidden className="h-px flex-1 bg-line" />
                  </div>
                </div>
              )}

              {mode === 'signup' && (
                <div>
                  <label htmlFor="signup-name" className="label">
                    Name
                  </label>
                  <input
                    id="signup-name"
                    type="text"
                    autoComplete="name"
                    value={displayName}
                    onChange={(e) => setDisplayName(e.target.value)}
                    className="field"
                  />
                </div>
              )}

              <div>
                <label htmlFor="auth-email" className="label">
                  Email
                </label>
                <input
                  id="auth-email"
                  type="email"
                  autoComplete="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="field"
                />
              </div>

              <div>
                <label htmlFor="auth-password" className="label">
                  Password
                </label>
                <input
                  id="auth-password"
                  type="password"
                  autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
                  required
                  minLength={8}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="field"
                />
                <p className="mt-1.5 text-[11px] text-fg-subtle">At least 8 characters.</p>
              </div>

              {error && (
                <p className={note('danger')} role="alert">
                  <span className="inline-flex items-start gap-1.5">
                    <AlertIcon className="mt-px h-3.5 w-3.5 shrink-0" />
                    {error}
                  </span>
                </p>
              )}

              <div className={segmentWrap} role="group" aria-label="Sign in or create an account">
                {(
                  [
                    ['signin', 'Sign in'],
                    ['signup', 'Create account'],
                  ] as const
                ).map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    aria-pressed={mode === value}
                    onClick={() => setMode(value)}
                    className={segmentItemClass(mode === value)}
                  >
                    {label}
                  </button>
                ))}
              </div>

              <button type="submit" disabled={busy} className={cx(btnPrimary, 'w-full')}>
                {busy
                  ? 'Working…'
                  : mode === 'signin'
                  ? 'Sign in'
                  : 'Create account'}
              </button>

              <div className="flex items-center justify-between gap-3 pt-1">
                <button
                  type="button"
                  onClick={() => {
                    setAuthView('forgot');
                    setError(null);
                    setNotice(null);
                  }}
                  className="text-xs text-fg-muted transition-colors hover:text-accent"
                >
                  Forgot password?
                </button>
              </div>

              {!config?.billing_enabled && (
                <p className="text-xs text-fg-subtle">
                  Billing is not enabled on this deployment — your free account
                  still works.
                </p>
              )}
            </form>
          )}

          {view === 'account' && signedIn && (
            <div className="space-y-5">
              <dl className="grid grid-cols-2 gap-3">
                <div className="rounded-lg border border-line bg-surface-2 px-4 py-3">
                  <dt className="text-xs text-fg-subtle">Plan</dt>
                  <dd className="mt-0.5 text-base font-semibold text-fg">
                    {entitlement?.plan_name}
                  </dd>
                  <dd className="mt-1 text-xs text-fg-muted tabular-nums">
                    {used} of {limit || '∞'} searches used
                  </dd>
                </div>
                <div className="rounded-lg border border-line bg-surface-2 px-4 py-3">
                  <dt className="text-xs text-fg-subtle">AI analyst</dt>
                  <dd className="mt-0.5 text-base font-semibold text-fg tabular-nums">
                    {entitlement?.analyst_used ?? 0}
                    <span className="text-sm font-normal text-fg-subtle">
                      {' '}
                      / {entitlement?.analyst_limit || '∞'}
                    </span>
                  </dd>
                  {entitlement?.period_end && (
                    <dd className="mt-1 text-xs text-fg-muted">
                      Renews {new Date(entitlement.period_end).toLocaleDateString()}
                    </dd>
                  )}
                </div>
              </dl>

              {entitlement?.email_verified === false && (
                <p className={note('warning')}>
                  Your email address has not been confirmed yet. Check your inbox,
                  or sign out and in again after confirming.
                </p>
              )}

              <form onSubmit={saveProfile} className="space-y-4 rounded-lg border border-line p-4">
                <div>
                  <h3 className="text-sm font-semibold text-fg">Academic profile</h3>
                  <p className="mt-0.5 text-xs text-fg-muted">
                    Shared with the lab so we can filter and sort our academic
                    customers.
                  </p>
                </div>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                  <div>
                    <label htmlFor="profile-academic-email" className="label">
                      Academic email
                    </label>
                    <input
                      id="profile-academic-email"
                      type="email"
                      value={profile.academic_email || ''}
                      onChange={(e) =>
                        setProfile({ ...profile, academic_email: e.target.value })
                      }
                      className="field"
                    />
                  </div>
                  <div>
                    <label htmlFor="profile-institute" className="label">
                      Institute / University
                    </label>
                    <input
                      id="profile-institute"
                      type="text"
                      value={profile.institute_name || ''}
                      onChange={(e) =>
                        setProfile({ ...profile, institute_name: e.target.value })
                      }
                      className="field"
                    />
                  </div>
                  <div>
                    <label htmlFor="profile-institute-id" className="label">
                      Institute ID
                    </label>
                    <input
                      id="profile-institute-id"
                      type="text"
                      value={profile.institute_id || ''}
                      onChange={(e) =>
                        setProfile({ ...profile, institute_id: e.target.value })
                      }
                      className="field"
                    />
                  </div>
                  <div>
                    <label htmlFor="profile-phone" className="label">
                      Phone
                    </label>
                    <input
                      id="profile-phone"
                      type="tel"
                      value={profile.phone || ''}
                      onChange={(e) => setProfile({ ...profile, phone: e.target.value })}
                      className="field"
                    />
                  </div>
                  <div className="sm:col-span-2">
                    <label htmlFor="profile-type" className="label">
                      Type of academician
                    </label>
                    <select
                      id="profile-type"
                      value={profile.academician_type || ''}
                      onChange={(e) =>
                        setProfile({ ...profile, academician_type: e.target.value })
                      }
                      className="field"
                    >
                      <option value="">Not set</option>
                      {ACADEMICIAN_TYPES.map((t) => (
                        <option key={t} value={t}>
                          {t}
                        </option>
                      ))}
                    </select>
                  </div>
                </div>
                {profileMsg && <p className={note('success')}>{profileMsg}</p>}
                {profileErr && (
                  <p className={note('danger')} role="alert">
                    {profileErr}
                  </p>
                )}
                <button type="submit" disabled={busy} className={btnSecondary}>
                  {busy ? 'Saving…' : 'Save profile'}
                </button>
              </form>

              <div className="flex flex-wrap gap-2">
                <button
                  onClick={() => {
                    setView('plans');
                    setCheckout({ phase: 'idle' });
                  }}
                  className={btnPrimary}
                >
                  Upgrade plan
                </button>
                <button onClick={signOut} className={btnSecondary}>
                  Sign out
                </button>
              </div>

              {error && <p className={note('warning')}>{error}</p>}
            </div>
          )}
        </div>
      </div>
    </div>
  ) : null;

  // The modal is rendered in a portal on <body>: the sticky header uses
  // backdrop-blur, which makes it the containing block for position:fixed
  // children — without this the panel would be positioned against the header
  // and appear hidden behind the page content.
  return (
    <>
      {trigger}
      {mounted && modal ? createPortal(modal, document.body) : null}
    </>
  );
}
