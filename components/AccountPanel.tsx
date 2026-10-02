'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
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

type View = 'account' | 'plans';
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
  const [checkout, setCheckout] = useState<CheckoutState>({ phase: 'idle' });
  const [mounted, setMounted] = useState(false);
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
    try {
      const { profile: saved } = await updateProfile(profile);
      setProfile({ ...EMPTY_PROFILE, ...saved });
      setProfileMsg('Profile saved.');
    } catch (err: any) {
      setProfileMsg(err.message || 'Could not save your profile');
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

  async function startCheckout(plan: Plan) {
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
      const order = await createOrder(plan.id);
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
          theme: { color: '#22d3ee' },
        });
        setCheckout({ phase: 'awaiting' });
        instance.open();
      });
    } catch (err: any) {
      setCheckout({ phase: 'error', message: err.message || 'Could not start the payment.' });
    }
  }

  const signedIn = entitlement?.signed_in ?? false;
  const used = entitlement?.searches_used ?? 0;
  const limit = entitlement?.searches_limit ?? 0;
  // Any in-flight checkout phase locks every plan card, so a second click can
  // never open a duplicate Razorpay modal.
  const checkoutBusy =
    checkout.phase === 'preparing' ||
    checkout.phase === 'awaiting' ||
    checkout.phase === 'verifying';

  const trigger = hideTrigger ? null : (
    <button
      onClick={() => {
        setError(null);
        setCheckout({ phase: 'idle' });
        setView(signedIn ? 'account' : 'plans');
        onOpen();
      }}
      className="px-3 py-1.5 text-sm font-medium text-gray-300 hover:text-cyan-400 border border-gray-800 hover:border-cyan-700 transition-colors"
    >
      {signedIn ? `${entitlement?.plan_name} · ${used}/${limit || '—'}` : 'Pricing'}
    </button>
  );

  const modal = isPanelOpen ? (
    <div className="fixed inset-0 z-[100] flex items-start justify-center overflow-y-auto bg-black/75 backdrop-blur-sm px-4 py-10">
      <div className="glass-card w-full max-w-2xl p-0 overflow-hidden">
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800/60">
          <div>
            <h3 className="text-base font-semibold text-gray-100">
              {view === 'plans' ? 'Plans' : 'Your account'}
            </h3>
            <p className="text-xs text-gray-500 mt-0.5">
              {view === 'plans'
                ? 'Pay securely by card, UPI or netbanking.'
                : signedIn
                ? entitlement?.email
                : 'Create a free account to run searches and track usage.'}
            </p>
          </div>
          <button onClick={onClose} className="text-sm text-gray-500 hover:text-gray-300 px-2 py-1">
            Close
          </button>
        </div>

        <div className="px-6 py-5 max-h-[70vh] overflow-y-auto">
          {notice && (
            <div className="mb-4 text-xs border border-cyan-900 text-cyan-300 rounded px-3 py-2">
              {notice}
            </div>
          )}

          {view === 'plans' && (
            <div className="space-y-4">
              {checkout.phase !== 'idle' && (
                <div className="text-xs border border-gray-800 rounded px-3 py-2">
                  {checkout.phase === 'preparing' && 'Preparing secure checkout…'}
                  {checkout.phase === 'awaiting' && 'Waiting for payment…'}
                  {checkout.phase === 'verifying' && 'Verifying payment…'}
                  {checkout.phase === 'done' && <span className="text-cyan-400">{checkout.message}</span>}
                  {checkout.phase === 'cancelled' && 'Payment cancelled. Nothing was charged.'}
                  {checkout.phase === 'error' && <span className="text-amber-400">{checkout.message}</span>}
                </div>
              )}

              {plans.filter((p) => p.price_inr > 0).length === 0 && (
                <p className="text-xs text-gray-500">
                  Plans could not be loaded. Please close this and try again.
                </p>
              )}

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {plans
                  .filter((p) => p.price_inr > 0)
                  .map((plan) => (
                    /* The whole card is the button: people click the price or the
                       feature list, not just the small label at the bottom. */
                    <button
                      key={plan.id}
                      type="button"
                      onClick={() => startCheckout(plan)}
                      disabled={checkoutBusy}
                      aria-busy={checkout.phase === 'preparing'}
                      className="text-left border border-gray-800 rounded p-4 flex flex-col hover:border-cyan-700 hover:bg-cyan-500/5 transition-colors disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:border-gray-800 disabled:hover:bg-transparent focus:outline-none focus-visible:border-cyan-600"
                    >
                      <span className="flex items-baseline justify-between">
                        <span className="text-sm font-semibold text-gray-100">{plan.name}</span>
                        <span className="text-sm tabular-nums text-cyan-400">
                          ₹{plan.price_inr.toLocaleString('en-IN')}
                          <span className="text-gray-500 text-xs">/mo</span>
                        </span>
                      </span>
                      {plan.tag && <span className="block text-xs text-gray-500 mt-1">{plan.tag}</span>}
                      <span className="mt-3 space-y-1 text-xs text-gray-400 flex-1 block">
                        {plan.features.map((f) => (
                          <span key={f} className="block">{f}</span>
                        ))}
                      </span>
                      <span className="mt-4 w-full block text-center text-sm font-medium py-2 border border-cyan-600 text-cyan-400">
                        {checkoutBusy ? 'Opening checkout…' : `Pay ₹${plan.price_inr.toLocaleString('en-IN')}`}
                      </span>
                    </button>
                  ))}
              </div>

              {error && <p className="text-xs text-amber-400">{error}</p>}
            </div>
          )}

          {view === 'account' && !signedIn && authView === 'forgot' && (
            <form onSubmit={submitForgot} className="space-y-3 max-w-sm">
              <p className="text-xs text-gray-400">
                Enter your email and we will send you a link to choose a new password.
              </p>
              <div>
                <label className="block text-xs text-gray-500 mb-1">Email</label>
                <input
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full bg-transparent border border-gray-800 px-3 py-2 text-sm text-gray-200 focus:border-cyan-600 focus:outline-none"
                />
              </div>
              {error && <p className="text-xs text-amber-400">{error}</p>}
              <button
                type="submit"
                disabled={busy}
                className="w-full text-sm font-medium py-2 border border-cyan-600 text-cyan-400 hover:bg-cyan-500/10 transition-colors disabled:opacity-50"
              >
                {busy ? 'Sending…' : 'Send reset link'}
              </button>
              <button
                type="button"
                onClick={() => {
                  setAuthView('form');
                  setError(null);
                }}
                className="text-xs text-gray-500 hover:text-gray-300"
              >
                Back to sign in
              </button>
            </form>
          )}

          {view === 'account' && !signedIn && authView === 'form' && (
            <form onSubmit={submitAuth} className="space-y-3 max-w-sm">
              {mode === 'signin' && supabase && (
                <div className="pb-1">
                  <button
                    type="button"
                    onClick={signInWithGoogle}
                    disabled={busy}
                    className="w-full flex items-center justify-center gap-3 border border-gray-700 hover:border-gray-500 text-gray-200 text-sm py-2 transition-colors disabled:opacity-50"
                  >
                    <GoogleMark />
                    Continue with Google
                  </button>
                  <div className="flex items-center gap-3 my-4">
                    <span className="h-px flex-1 bg-gray-800" />
                    <span className="text-[10px] uppercase tracking-widest text-gray-600">
                      or
                    </span>
                    <span className="h-px flex-1 bg-gray-800" />
                  </div>
                </div>
              )}
              {mode === 'signup' && (
                <div>
                  <label className="block text-xs text-gray-500 mb-1">Name</label>
                  <input
                    type="text"
                    value={displayName}
                    onChange={(e) => setDisplayName(e.target.value)}
                    className="w-full bg-transparent border border-gray-800 px-3 py-2 text-sm text-gray-200 focus:border-cyan-600 focus:outline-none"
                  />
                </div>
              )}
              <div>
                <label className="block text-xs text-gray-500 mb-1">Email</label>
                <input
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="w-full bg-transparent border border-gray-800 px-3 py-2 text-sm text-gray-200 focus:border-cyan-600 focus:outline-none"
                />
              </div>
              <div>
                <label className="block text-xs text-gray-500 mb-1">Password</label>
                <input
                  type="password"
                  required
                  minLength={8}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full bg-transparent border border-gray-800 px-3 py-2 text-sm text-gray-200 focus:border-cyan-600 focus:outline-none"
                />
              </div>
              {error && <p className="text-xs text-amber-400">{error}</p>}
              <button
                type="submit"
                disabled={busy}
                className="w-full text-sm font-medium py-2 border border-cyan-600 text-cyan-400 hover:bg-cyan-500/10 transition-colors disabled:opacity-50"
              >
                {busy ? 'Working…' : mode === 'signin' ? 'Sign in' : 'Create account'}
              </button>
              <div className="flex items-center justify-between pt-1">
                <button
                  type="button"
                  onClick={() => setMode(mode === 'signin' ? 'signup' : 'signin')}
                  className="text-xs text-gray-500 hover:text-gray-300"
                >
                  {mode === 'signin' ? 'Need an account? Create one' : 'Already have an account? Sign in'}
                </button>
                {mode === 'signin' && (
                  <button
                    type="button"
                    onClick={() => {
                      setAuthView('forgot');
                      setError(null);
                      setNotice(null);
                    }}
                    className="text-xs text-gray-500 hover:text-cyan-400"
                  >
                    Forgot password?
                  </button>
                )}
              </div>
              {!config?.billing_enabled && (
                <p className="text-xs text-gray-600">
                  Billing is not enabled on this deployment — your free account still works.
                </p>
              )}
            </form>
          )}

          {view === 'account' && signedIn && (
            <div className="space-y-4">
              <div className="grid grid-cols-2 gap-3">
                <div className="border border-gray-800 p-4">
                  <div className="text-xs text-gray-500">Plan</div>
                  <div className="text-lg font-semibold text-gray-100">{entitlement?.plan_name}</div>
                  <div className="text-xs text-gray-500 mt-1 tabular-nums">
                    {used} of {limit || '∞'} searches used
                  </div>
                </div>
                <div className="border border-gray-800 p-4">
                  <div className="text-xs text-gray-500">AI analyst</div>
                  <div className="text-lg font-semibold text-gray-100 tabular-nums">
                    {entitlement?.analyst_used ?? 0}
                    <span className="text-sm text-gray-500"> / {entitlement?.analyst_limit || '∞'}</span>
                  </div>
                  {entitlement?.period_end && (
                    <div className="text-xs text-gray-500 mt-1">
                      Renews {new Date(entitlement.period_end).toLocaleDateString()}
                    </div>
                  )}
                </div>
              </div>

              {entitlement?.email_verified === false && (
                <div className="border border-amber-900/70 text-amber-300/90 text-xs rounded px-3 py-2">
                  Your email address has not been confirmed yet. Check your inbox, or sign out
                  and in again after confirming.
                </div>
              )}

              <form onSubmit={saveProfile} className="space-y-3 border border-gray-800 p-4">
                <div>
                  <h4 className="text-sm font-semibold text-gray-100">Academic profile</h4>
                  <p className="text-xs text-gray-500 mt-0.5">
                    Shared with the lab so we can filter and sort our academic customers.
                  </p>
                </div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">Academic email</label>
                    <input
                      type="email"
                      value={profile.academic_email || ''}
                      onChange={(e) => setProfile({ ...profile, academic_email: e.target.value })}
                      className="w-full bg-transparent border border-gray-800 px-3 py-1.5 text-sm text-gray-200 focus:border-cyan-600 focus:outline-none"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">Institute / University</label>
                    <input
                      type="text"
                      value={profile.institute_name || ''}
                      onChange={(e) => setProfile({ ...profile, institute_name: e.target.value })}
                      className="w-full bg-transparent border border-gray-800 px-3 py-1.5 text-sm text-gray-200 focus:border-cyan-600 focus:outline-none"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">Institute ID</label>
                    <input
                      type="text"
                      value={profile.institute_id || ''}
                      onChange={(e) => setProfile({ ...profile, institute_id: e.target.value })}
                      className="w-full bg-transparent border border-gray-800 px-3 py-1.5 text-sm text-gray-200 focus:border-cyan-600 focus:outline-none"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">Phone</label>
                    <input
                      type="tel"
                      value={profile.phone || ''}
                      onChange={(e) => setProfile({ ...profile, phone: e.target.value })}
                      className="w-full bg-transparent border border-gray-800 px-3 py-1.5 text-sm text-gray-200 focus:border-cyan-600 focus:outline-none"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-gray-500 mb-1">Type of academician</label>
                    <select
                      value={profile.academician_type || ''}
                      onChange={(e) => setProfile({ ...profile, academician_type: e.target.value })}
                      className="w-full bg-gray-900 border border-gray-800 px-3 py-1.5 text-sm text-gray-200 focus:border-cyan-600 focus:outline-none"
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
                {profileMsg && (
                  <p className={`text-xs ${profileMsg.endsWith('.') ? 'text-cyan-400' : 'text-amber-400'}`}>
                    {profileMsg}
                  </p>
                )}
                <button
                  type="submit"
                  disabled={busy}
                  className="text-sm px-4 py-2 border border-cyan-600 text-cyan-400 hover:bg-cyan-500/10 transition-colors disabled:opacity-50"
                >
                  {busy ? 'Saving…' : 'Save profile'}
                </button>
              </form>

              <div className="flex gap-2">
                <button
                  onClick={() => {
                    setView('plans');
                    setCheckout({ phase: 'idle' });
                  }}
                  className="text-sm px-4 py-2 border border-cyan-600 text-cyan-400 hover:bg-cyan-500/10 transition-colors"
                >
                  Upgrade plan
                </button>
                <button
                  onClick={signOut}
                  className="text-sm px-4 py-2 border border-gray-800 text-gray-400 hover:text-gray-200 transition-colors"
                >
                  Sign out
                </button>
              </div>

              {error && <p className="text-xs text-amber-400">{error}</p>}
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