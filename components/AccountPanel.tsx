'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  BillingConfig,
  Entitlement,
  Plan,
  createOrder,
  getConfig,
  getMe,
  getPlans,
  loadCheckoutScript,
  login,
  logout,
  paymentStatus,
  register,
  verifyPayment,
} from '@/lib/billing';

type View = 'account' | 'plans';
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
}

export default function AccountPanel({ open, onOpen, onClose, onChange }: Props) {
  const [config, setConfig] = useState<BillingConfig | null>(null);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [entitlement, setEntitlement] = useState<Entitlement | null>(null);
  const [view, setView] = useState<View>('account');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [mode, setMode] = useState<'signin' | 'signup'>('signin');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [checkout, setCheckout] = useState<CheckoutState>({ phase: 'idle' });

  const refresh = useCallback(async () => {
    try {
      const [me, cfg] = await Promise.all([getMe(), config ? Promise.resolve(config) : getConfig()]);
      setEntitlement(me.entitlement);
      setConfig(cfg);
      onChange?.(me.entitlement);
    } catch {
      /* stay anonymous if the account service is unavailable */
    }
  }, [config, onChange]);

  useEffect(() => {
    void refresh();
    getPlans().then((r) => setPlans(r.plans)).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
              setCheckout({
                phase: 'error',
                message: err.message || 'We could not verify this payment. If you were charged, contact support with your payment id.',
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

  return (
    <>
      <button
        onClick={onOpen}
        className="px-3 py-1.5 text-sm font-medium text-gray-400 hover:text-gray-200 border border-transparent hover:border-gray-700 transition-colors"
      >
        {signedIn ? `${entitlement?.plan_name} · ${used}/${limit || '—'}` : 'Sign in'}
      </button>

      {open && (
        <div className="fixed inset-0 z-[70] flex items-start justify-center overflow-y-auto bg-black/70 backdrop-blur-sm px-4 py-10">
          <div className="glass-card w-full max-w-2xl p-0 overflow-hidden">
            <div className="flex items-center justify-between px-6 py-4 border-b border-gray-800/60">
              <div>
                <h3 className="text-base font-semibold text-gray-100">
                  {view === 'plans' ? 'Choose a plan' : 'Your account'}
                </h3>
                <p className="text-xs text-gray-500 mt-0.5">
                  {view === 'plans'
                    ? 'Pay securely by card, UPI or netbanking.'
                    : signedIn
                      ? entitlement?.email
                      : 'Create a free account to run searches and track usage.'}
                </p>
              </div>
              <button
                onClick={onClose}
                className="text-sm text-gray-500 hover:text-gray-300 px-2 py-1"
              >
                Close
              </button>
            </div>

            <div className="px-6 py-5 max-h-[70vh] overflow-y-auto">
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
                </div>
              )}

              {view === 'account' && !signedIn && (
                <form onSubmit={submitAuth} className="space-y-3 max-w-sm">
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
                  <button
                    type="button"
                    onClick={() => setMode(mode === 'signin' ? 'signup' : 'signin')}
                    className="text-xs text-gray-500 hover:text-gray-300"
                  >
                    {mode === 'signin' ? 'Need an account? Create one' : 'Already have an account? Sign in'}
                  </button>
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
      )}
    </>
  );
}