'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  AcademicProfile,
  BillingConfig,
  Entitlement,
  Plan,
  createOrder,
  forgotPassword,
  getConfig,
  getMe,
  getPlans,
  getProfile,
  googleSignIn,
  loadCheckoutScript,
  login,
  logout,
  paymentStatus,
  register,
  resendVerification,
  resetPassword,
  updateProfile,
  verifyPayment,
} from '@/lib/billing';

type View = 'account' | 'plans';
type AuthView = 'form' | 'forgot' | 'reset';

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
  /** Render the panel as soon as it mounts — used by the emailed
   *  /reset-password and /verify-email links. */
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
  const [opened, setOpened] = useState(false);
  const [password, setPassword] = useState('');
  const [mode, setMode] = useState<'signin' | 'signup'>('signin');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [authView, setAuthView] = useState<AuthView>('form');
  const [resetToken, setResetToken] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [profile, setProfile] = useState<AcademicProfile>(EMPTY_PROFILE);
  const [profileMsg, setProfileMsg] = useState<string | null>(null);
  const [checkout, setCheckout] = useState<CheckoutState>({ phase: 'idle' });
  const [mounted, setMounted] = useState(false);
  const googleButtonRef = useRef<HTMLDivElement | null>(null);
  // The host page owns `open`; the emailed-link pages own `opened`.
  const isPanelOpen = hideTrigger ? opened : open;

  // ── "Sign in with Google" ───────────────────────────────────────────────
  // Google Identity Services renders the button into our own div. The
  // credential it returns is a JWT the server verifies; nothing is trusted
  // from the browser.
  const handleGoogleCredential = useCallback(
    async (response: { credential?: string }) => {
      if (!response?.credential) return;
      setBusy(true);
      setError(null);
      try {
        const result = await googleSignIn(response.credential);
        setEntitlement(result.entitlement);
        onChange?.(result.entitlement);
        setNotice(`Signed in as ${result.user.email}.`);
        setView('account');
      } catch (err: any) {
        setError(err.message || 'Google sign-in was not accepted');
      } finally {
        setBusy(false);
      }
    },
    [onChange],
  );

  const clientId = config?.google_client_id;
  useEffect(() => {
    if (!clientId || !googleButtonRef.current) return;
    let cancelled = false;
    const render = () => {
      const target = googleButtonRef.current;
      const google = (window as any).google;
      if (cancelled || !target || !google?.accounts?.id) return;
      target.innerHTML = ''; // renderButton appends; clear to avoid duplicates
      google.accounts.id.initialize({
        client_id: clientId,
        callback: handleGoogleCredential,
      });
      google.accounts.id.renderButton(target, {
        theme: 'filled_black',
        size: 'medium',
        width: 280,
        text: 'continue_with',
      });
    };
    if ((window as any).google?.accounts?.id) {
      render();
    } else {
      const script = document.createElement('script');
      script.src = 'https://accounts.google.com/gsi/client';
      script.async = true;
      script.defer = true;
      script.onload = render;
      script.onerror = () =>
        setError('Could not load Google sign-in. Check your connection and try again.');
      document.body.appendChild(script);
    }
    return () => {
      cancelled = true;
    };
  }, [clientId, handleGoogleCredential, isPanelOpen]);

  useEffect(() => setMounted(true), []);

  const loadProfile = useCallback(async () => {
    if (typeof window === 'undefined') return;
    try {
      const { profile: p } = await getProfile();
      setProfile({ ...EMPTY_PROFILE, ...p });
    } catch {
      /* profile is optional; ignore */
    }
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [me, cfg] = await Promise.all([getMe(), config ? Promise.resolve(config) : getConfig()]);
      setEntitlement(me.entitlement);
      setConfig(cfg);
      onChange?.(me.entitlement);
      if (me.entitlement.signed_in) void loadProfile();
    } catch {
      /* stay anonymous if the account service is unavailable */
    }
  }, [config, onChange, loadProfile]);

  // The reset link in the password email lands on /reset-password?token=… —
  // keep the token and show the new-password form. Email confirmation has its
  // own page (/verify-email) and is handled there.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const token = new URLSearchParams(window.location.search).get('token');
    if (!token) return;
    if (!window.location.pathname.replace(/\/$/, '').endsWith('/reset-password')) return;
    setResetToken(token);
    setAuthView('reset');
    setView('account');
    onOpen();
  }, [onOpen]);

  useEffect(() => {
    void refresh();
    getPlans().then((r) => setPlans(r.plans)).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
    setBusy(true);
    setError(null);
    try {
      const result = mode === 'signin' ? await login(email, password) : await register(email, password);
      setEntitlement(result.entitlement);
      onChange?.(result.entitlement);
      setPassword('');
      setView('account');
    } catch (err: any) {
      setError(err.message || 'Could not sign in');
    } finally {
      setBusy(false);
    }
  }

  async function submitForgot(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await forgotPassword(email);
      setNotice(res.dev_link ? `${res.message} (dev: ${res.dev_link})` : res.message);
    } catch (err: any) {
      setError(err.message || 'Could not send the reset link');
    } finally {
      setBusy(false);
    }
  }

  async function submitReset(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const res = await resetPassword(resetToken, newPassword);
      setNotice(res.message);
      setNewPassword('');
      setAuthView('form');
      setMode('signin');
    } catch (err: any) {
      setError(err.message || 'Could not reset your password');
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
    await logout().catch(() => {});
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

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {plans
                  .filter((p) => p.price_inr > 0)
                  .map((plan) => (
                    <div key={plan.id} className="border border-gray-800 rounded p-4 flex flex-col">
                      <div className="flex items-baseline justify-between">
                        <span className="text-sm font-semibold text-gray-100">{plan.name}</span>
                        <span className="text-sm tabular-nums text-cyan-400">
                          ₹{plan.price_inr.toLocaleString('en-IN')}
                          <span className="text-gray-500 text-xs">/mo</span>
                        </span>
                      </div>
                      {plan.tag && <p className="text-xs text-gray-500 mt-1">{plan.tag}</p>}
                      <ul className="mt-3 space-y-1 text-xs text-gray-400 flex-1">
                        {plan.features.map((f) => (
                          <li key={f}>{f}</li>
                        ))}
                      </ul>
                      <button
                        onClick={() => startCheckout(plan)}
                        disabled={checkout.phase === 'awaiting' || checkout.phase === 'verifying'}
                        className="mt-4 w-full text-sm font-medium py-2 border border-cyan-600 text-cyan-400 hover:bg-cyan-500/10 transition-colors disabled:opacity-50"
                      >
                        Pay ₹{plan.price_inr.toLocaleString('en-IN')}
                      </button>
                    </div>
                  ))}
              </div>

              {error && <p className="text-xs text-amber-400">{error}</p>}
            </div>
          )}

          {view === 'account' && !signedIn && authView === 'reset' && (
            <form onSubmit={submitReset} className="space-y-3 max-w-sm">
              <p className="text-xs text-gray-400">Choose a new password for your account.</p>
              <div>
                <label className="block text-xs text-gray-500 mb-1">New password</label>
                <input
                  type="password"
                  required
                  minLength={8}
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  className="w-full bg-transparent border border-gray-800 px-3 py-2 text-sm text-gray-200 focus:border-cyan-600 focus:outline-none"
                />
              </div>
              {error && <p className="text-xs text-amber-400">{error}</p>}
              <button
                type="submit"
                disabled={busy}
                className="w-full text-sm font-medium py-2 border border-cyan-600 text-cyan-400 hover:bg-cyan-500/10 transition-colors disabled:opacity-50"
              >
                {busy ? 'Saving…' : 'Set new password'}
              </button>
            </form>
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
              {clientId && (
                <div className="pb-1">
                  <div ref={googleButtonRef} className="flex justify-center min-h-[44px]" />
                  <div className="flex items-center gap-3 my-4">
                    <span className="h-px flex-1 bg-gray-800" />
                    <span className="text-[10px] uppercase tracking-widest text-gray-600">
                      or
                    </span>
                    <span className="h-px flex-1 bg-gray-800" />
                  </div>
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
                <div className="border border-amber-900/70 text-amber-300/90 text-xs rounded px-3 py-2 flex items-center justify-between gap-3">
                  <span>Your email address is not confirmed yet.</span>
                  <button
                    onClick={async () => {
                      try {
                        const res = await resendVerification(entitlement.email || '');
                        setNotice(res.message);
                      } catch (err: any) {
                        setError(err.message || 'Could not send the confirmation link');
                      }
                    }}
                    className="shrink-0 underline hover:text-amber-200"
                  >
                    Resend link
                  </button>
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