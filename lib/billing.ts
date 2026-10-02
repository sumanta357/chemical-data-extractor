/**
 * Accounts, plans and payments (Razorpay) — browser client.
 *
 * The publishable key id is fetched from the server (never bundled), and the
 * key secret never exists in the browser at all.
 */

export interface Plan {
  id: string;
  name: string;
  price_inr: number;
  searches: number;
  analyst_messages: number;
  tag?: string | null;
  features: string[];
}

export interface Entitlement {
  signed_in: boolean;
  user_id?: number;
  email?: string;
  name?: string | null;
  plan: string;
  plan_name: string;
  plan_status?: string;
  searches_limit: number;
  searches_used: number;
  searches_remaining: number;
  analyst_limit: number;
  analyst_used: number;
  analyst_remaining: number;
  period_end?: string | null;
  billing_enabled: boolean;
  auth_required: boolean;
  status: string;
}

export interface BillingConfig {
  billing_enabled: boolean;
  key_id: string | null;
  currency: string;
  auth_required: boolean;
  checkout_script: string;
}

async function api(path: string, init?: RequestInit): Promise<any> {
  const res = await fetch(path, {
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
  });
  const text = await res.text();
  let body: any = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  if (!res.ok) {
    const detail = body?.detail;
    const message =
      typeof detail === 'string'
        ? detail
        : detail?.message || body?.message || `Request failed (${res.status})`;
    const err: any = new Error(message);
    err.status = res.status;
    err.code = typeof detail === 'object' ? detail?.code : undefined;
    throw err;
  }
  return body;
}

export const getConfig = () => api('/api/billing/config') as Promise<BillingConfig>;
export const getPlans = () => api('/api/billing/plans') as Promise<{ plans: Plan[] }>;
export const getMe = () =>
  api('/api/account/me') as Promise<{ user: any | null; entitlement: Entitlement }>;

export const register = (email: string, password: string, name?: string) =>
  api('/api/account/register', {
    method: 'POST',
    body: JSON.stringify({ email, password, name: name || null }),
  }) as Promise<{ user: any; entitlement: Entitlement }>;

export const login = (email: string, password: string) =>
  api('/api/account/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  }) as Promise<{ user: any; entitlement: Entitlement }>;

export const logout = () => api('/api/account/logout', { method: 'POST' });

export const createOrder = (plan: string) =>
  api('/api/billing/create-order', {
    method: 'POST',
    body: JSON.stringify({ plan }),
  }) as Promise<{
    order_id: string;
    amount: number;
    currency: string;
    key_id: string;
    plan: string;
    plan_name: string;
  }>;

export const verifyPayment = (payload: {
  razorpay_order_id: string;
  razorpay_payment_id: string;
  razorpay_signature: string;
}) =>
  api('/api/billing/verify-payment', {
    method: 'POST',
    body: JSON.stringify(payload),
  }) as Promise<{ status: string; message: string; entitlement: Entitlement }>;

export const paymentStatus = (paymentId: string) =>
  api(`/api/billing/payment-status/${encodeURIComponent(paymentId)}`) as Promise<{
    payment_id: string;
    status: string;
    plan: string | null;
    entitlement: Entitlement;
  }>;

export function loadCheckoutScript(src: string): Promise<boolean> {
  return new Promise((resolve) => {
    if (typeof window === 'undefined') return resolve(false);
    if ((window as any).Razorpay) return resolve(true);
    const existing = document.querySelector(`script[src="${src}"]`);
    if (existing) {
      existing.addEventListener('load', () => resolve(true));
      existing.addEventListener('error', () => resolve(false));
      return;
    }
    const script = document.createElement('script');
    script.src = src;
    script.async = true;
    script.onload = () => resolve(true);
    script.onerror = () => resolve(false);
    document.body.appendChild(script);
  });
}